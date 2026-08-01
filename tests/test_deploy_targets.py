import unittest

from deploy.contabo.deploy_targets import DOCKER_SERVICES, deployment_plan


class DeploymentTargetTests(unittest.TestCase):
    def test_nifty_change_does_not_touch_crude_or_cron(self):
        plan = deployment_plan([
            "options/nifty_ws.py",
            "options/nifty_feature_backfill.py",
            "options/schema.sql",
        ])
        self.assertTrue(plan.deploy_web)
        self.assertEqual(plan.services, ("nifty-ws",))

    def test_schema_documentation_change_is_source_only(self):
        plan = deployment_plan(["options/schema.sql"])
        self.assertFalse(plan.deploy_web)
        self.assertEqual(plan.services, ())

    def test_crude_change_does_not_touch_nifty_or_cron(self):
        plan = deployment_plan(["options/crudeoil_ws.py"])
        self.assertTrue(plan.deploy_web)
        self.assertEqual(plan.services, ("crudeoil-ws",))

    def test_cron_collector_change_only_restarts_cron(self):
        plan = deployment_plan(["options/mcx_ohlc.py"])
        self.assertTrue(plan.deploy_web)
        self.assertEqual(plan.services, ("cron-worker",))

    def test_shared_runtime_change_deploys_every_service(self):
        plan = deployment_plan(["requirements.txt"])
        self.assertTrue(plan.deploy_web)
        self.assertEqual(plan.services, DOCKER_SERVICES)

    def test_docs_and_tests_do_not_restart_runtime(self):
        plan = deployment_plan(["README.md", "tests/test_nifty_ingestion.py"])
        self.assertFalse(plan.deploy_web)
        self.assertEqual(plan.services, ())

    def test_manual_dispatch_deploys_everything(self):
        plan = deployment_plan([], deploy_all=True)
        self.assertTrue(plan.deploy_web)
        self.assertEqual(plan.services, DOCKER_SERVICES)


if __name__ == "__main__":
    unittest.main()
