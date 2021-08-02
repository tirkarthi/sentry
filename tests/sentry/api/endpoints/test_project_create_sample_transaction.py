from django.urls import reverse

from sentry.testutils import APITestCase


class ProjectCreateSampleTransactionTest(APITestCase):
    def setUp(self):
        super().setUp()
        self.login_as(user=self.user)
        self.team = self.create_team()

    def test_no_platform(self):
        project = self.create_project(teams=[self.team], name="foo", platform=None)

        url = reverse(
            "sentry-api-0-project-create-sample-transaction",
            kwargs={"organization_slug": project.organization.slug, "project_slug": project.slug},
        )
        response = self.client.post(url, format="json")

        assert response.status_code == 200
        assert response.data["title"] == "/productstore"

    def test_react(self):
        project = self.create_project(teams=[self.team], name="foo", platform="javascript-react")

        url = reverse(
            "sentry-api-0-project-create-sample-transaction",
            kwargs={"organization_slug": project.organization.slug, "project_slug": project.slug},
        )
        response = self.client.post(url, format="json")

        assert response.status_code == 200
        assert response.data["title"] == "/productstore"

    def test_django(self):
        project = self.create_project(teams=[self.team], name="foo", platform="python-django")

        url = reverse(
            "sentry-api-0-project-create-sample-transaction",
            kwargs={"organization_slug": project.organization.slug, "project_slug": project.slug},
        )
        response = self.client.post(url, format="json")

        assert response.status_code == 200
        assert response.data["title"] == "getProductList"

    def test_ios(self):
        project = self.create_project(teams=[self.team], name="foo", platform="apple-ios")

        url = reverse(
            "sentry-api-0-project-create-sample-transaction",
            kwargs={"organization_slug": project.organization.slug, "project_slug": project.slug},
        )
        response = self.client.post(url, format="json")

        assert response.status_code == 200
        assert response.data["title"] == "iOS_Swift.ViewController"

    def test_other_platform(self):
        project = self.create_project(teams=[self.team], name="foo", platform="other")

        url = reverse(
            "sentry-api-0-project-create-sample-transaction",
            kwargs={"organization_slug": project.organization.slug, "project_slug": project.slug},
        )
        response = self.client.post(url, format="json")

        assert response.status_code == 200
        assert response.data["title"] == "/productstore"