from __future__ import annotations

import logging
from types import LambdaType
from typing import Any, Mapping, Sequence

from django.views import View
from rest_framework.request import Request
from rest_framework.response import Response

from sentry import analytics
from sentry.db.models import Model
from sentry.models import Organization
from sentry.utils.hashlib import md5_text
from sentry.web.helpers import render_to_response

from .constants import INTEGRATION_EXPIRATION_TTL
from .store import PipelineSessionStore
from .types import PipelineAnalyticsEntry, PipelineRequestState


class Pipeline:
    """
    Pipeline provides a mechanism to guide the user through a request
    'pipeline', where each view may be completed by calling the ``next_step``
    pipeline method to traverse through the pipe.

    The pipeline works with a PipelineProvider object which provides the
    pipeline views and is made available to the views through the passed in
    pipeline.

    :provider_manager:
    A class property that must be specified to allow for lookup of a provider
    implementation object given it's key.

    :provider_model_cls:
    The Provider model object represents the instance of an object implementing
    the PipelineProvider interface. This is used to look up the instance
    when constructing an in progress pipeline (get_for_request).

    :config:
    A object that specifies additional pipeline and provider runtime
    configurations. An example of usage is for OAuth Identity providers, for
    overriding the scopes. The config object will be passed into the provider
    using the ``update_config`` method.
    """

    pipeline_name = None
    provider_manager = None
    provider_model_cls = None
    session_store_cls = PipelineSessionStore

    @classmethod
    def get_for_request(cls, request: Request) -> Pipeline | None:
        req_state = cls.unpack_state(request)
        if not req_state:
            return None

        config = req_state.state.config
        return cls(
            request,
            organization=req_state.organization,
            provider_key=req_state.provider_key,
            provider_model=req_state.provider_model,
            config=config,
        )

    @classmethod
    def unpack_state(cls, request: Request) -> PipelineRequestState | None:
        state = cls.session_store_cls(request, cls.pipeline_name, ttl=INTEGRATION_EXPIRATION_TTL)
        if not state.is_valid():
            return None

        provider_model = None
        if state.provider_model_id:
            provider_model = cls.provider_model_cls.objects.get(id=state.provider_model_id)

        organization = None
        if state.org_id:
            organization = Organization.objects.get(id=state.org_id)

        provider_key = state.provider_key

        return PipelineRequestState(state, provider_model, organization, provider_key)

    def get_provider(self, provider_key: str):
        return self.provider_manager.get(provider_key)

    def __init__(
        self,
        request: Request,
        provider_key: str,
        organization: Organization | None = None,
        provider_model: Model | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.request = request
        self.organization = organization
        self.state = self.session_store_cls(
            request, self.pipeline_name, ttl=INTEGRATION_EXPIRATION_TTL
        )
        self.provider_model = provider_model
        self.provider = self.get_provider(provider_key)

        self.config = config or {}
        self.provider.set_pipeline(self)
        self.provider.update_config(self.config)

        self.pipeline_views = self.get_pipeline_views()

        # we serialize the pipeline to be ['fqn.PipelineView', ...] which
        # allows us to determine if the pipeline has changed during the auth
        # flow or if the user is somehow circumventing a chunk of it
        pipe_ids = [f"{type(v).__module__}.{type(v).__name__}" for v in self.pipeline_views]
        self.signature = md5_text(*pipe_ids).hexdigest()

    def get_pipeline_views(self) -> Sequence[View]:
        """
        Retrieve the pipeline views from the provider.

        You may wish to override this method to provide views that all
        providers should inherit, or customize the provider method called to
        retrieve the views.
        """
        return self.provider.get_pipeline_views()

    def is_valid(self) -> bool:
        return self.state.is_valid() and self.state.signature == self.signature

    def initialize(self) -> None:
        self.state.regenerate(self.get_initial_state())

    def get_initial_state(self) -> Mapping[str, Any]:
        return {
            "uid": self.request.user.id if self.request.user.is_authenticated else None,
            "provider_model_id": self.provider_model.id if self.provider_model else None,
            "provider_key": self.provider.key,
            "org_id": self.organization.id if self.organization else None,
            "step_index": 0,
            "signature": self.signature,
            "config": self.config,
            "data": {},
        }

    def clear_session(self) -> None:
        self.state.clear()

    def current_step(self) -> Response:
        """
        Render the current step.
        """
        step_index = self.state.step_index

        if step_index == len(self.pipeline_views):
            return self.finish_pipeline()

        step = self.pipeline_views[step_index]

        # support late binding steps
        if isinstance(step, LambdaType):
            step = step()

        return self.dispatch_to(step)

    def dispatch_to(self, step: View) -> Response:
        """Dispatch to a view expected by this pipeline.

        A subclass may override this if its views take other parameters.
        """
        return step.dispatch(request=self.request, pipeline=self)

    def error(self, message: str) -> Response:
        context = {"error": message}
        extra = {
            "organization_id": self.organization.id if self.organization else None,
            "provider": self.provider.key,
            "error": message,
        }
        logger = self.get_logger()
        # log error
        logger.error("pipeline error", extra=extra)
        return render_to_response("sentry/pipeline-error.html", context, self.request)

    def render_warning(self, message: str) -> Response:
        """For situations when we want to display an error without triggering an issue"""
        context = {"error": message}
        return render_to_response("sentry/pipeline-provider-error.html", context, self.request)

    def next_step(self, step_size: int = 1) -> Response:
        """
        Render the next step.
        """
        self.state.step_index += step_size

        analytics_entry = self.get_analytics_entry()
        if analytics_entry and self.organization:
            analytics.record(
                analytics_entry.event_type,
                user_id=self.request.user.id,
                organization_id=self.organization.id,
                integration=self.provider.key,
                step_index=self.state.step_index,
                pipeline_type=analytics_entry.pipeline_type,
            )

        return self.current_step()

    def get_analytics_entry(self) -> PipelineAnalyticsEntry | None:
        """Return analytics attributes for this pipeline."""
        return None

    def finish_pipeline(self) -> None:
        """
        Called when the pipeline completes the final step.
        """
        raise NotImplementedError

    def bind_state(self, key: str, value: Any) -> None:
        data = self.state.data or {}
        data[key] = value

        self.state.data = data

    def fetch_state(self, key: str | None = None) -> Mapping[str, Any] | None:
        data = self.state.data
        if not data:
            return None
        return data if key is None else data.get(key)

    def get_logger(self) -> logging.Logger:
        return logging.getLogger(f"sentry.integration.{self.provider.key}")
