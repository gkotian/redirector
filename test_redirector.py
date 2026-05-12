import unittest
from urllib.parse import urlparse

import redirector


class RancherCronjobJobLookupTests(unittest.TestCase):
    def test_identifies_cronjob_from_generated_job_page(self):
        url = (
            "https://rancher.dev.rke2-cnvx.com/dashboard/c/local/explorer/"
            "batch.job/cnvx-controlplane/"
            "controlplane-empire-client-29643000#pods"
        )

        entry = redirector.identify_app(url)

        self.assertIsNotNone(entry)
        self.assertEqual("ControlPlane.Empire.Client", entry["repo_name"])
        self.assertEqual("cronjob", entry["kubernetes_workload_type"])
        self.assertEqual(
            "https://rancher.rke2-cnvx.com/dashboard/c/local/explorer/"
            "batch.cronjob/cnvx-controlplane/controlplane-empire-client#jobs",
            redirector.build_target(entry, "rancher"),
        )

    def test_parse_rancher_resource_includes_resource_type(self):
        parsed = urlparse(
            "https://rancher.dev.rke2-cnvx.com/dashboard/c/local/explorer/"
            "batch.job/cnvx-controlplane/"
            "controlplane-empire-client-29643000#pods"
        )

        self.assertEqual(
            (
                "batch.job",
                "cnvx-controlplane",
                "controlplane-empire-client-29643000",
            ),
            redirector.parse_rancher_resource(parsed),
        )

    def test_cronjob_workload_name_requires_numeric_suffix(self):
        self.assertEqual(
            "controlplane-empire-client",
            redirector.cronjob_workload_name_from_job_name(
                "controlplane-empire-client-29643000"
            ),
        )
        self.assertEqual(
            "",
            redirector.cronjob_workload_name_from_job_name(
                "controlplane-empire-client-manual"
            ),
        )


if __name__ == "__main__":
    unittest.main()
