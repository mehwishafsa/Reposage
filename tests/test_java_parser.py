"""Java: types, methods, Javadoc, imports and typed call resolution."""

import unittest

from tests.helpers import copy_fixture, edges, node, node_ids, scan

P = "src/main/java/com/shop/"
APP, ORDER, STATUS = P + "App.java", P + "model/Order.java", P + "model/Status.java"
SVC, HELP = P + "service/OrderService.java", P + "service/Helpers.java"
REPO, IREPO = P + "repo/OrderRepository.java", P + "repo/Repository.java"


class JavaParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph, _ = scan(copy_fixture("java_project"))

    def test_type_kinds(self):
        self.assertEqual(node(self.graph, f"{STATUS}::Status")["type"], "enum")
        self.assertEqual(node(self.graph, f"{IREPO}::Repository")["type"], "interface")
        self.assertEqual(node(self.graph, f"{ORDER}::Order.describe")["type"], "method")

    def test_overloads_get_distinct_ids(self):
        ids = node_ids(self.graph, "method")
        self.assertIn(f"{SVC}::OrderService.place", ids)
        self.assertIn(f"{SVC}::OrderService.place#2", ids)

    def test_javadoc_and_signature(self):
        order = node(self.graph, f"{ORDER}::Order")
        self.assertEqual(order["doc"], "An order for a single item.")
        # Annotations like @Override are left out of the signature.
        self.assertEqual(node(self.graph, f"{ORDER}::Order.toString")["signature"],
                         "public String toString()")

    def test_imports(self):
        imports = edges(self.graph, "imports")
        self.assertIn((APP, SVC), imports)
        self.assertIn((APP, ORDER), imports)    # import com.shop.model.*;
        self.assertIn((APP, STATUS), imports)
        self.assertEqual(node(self.graph, REPO)["external_imports"],
                         ["java.util.ArrayList", "java.util.List"])

    def test_calls(self):
        calls = edges(self.graph, "calls")
        expected = {
            (f"{APP}::App.main", f"{SVC}::OrderService"),            # new OrderService()
            (f"{APP}::App.main", f"{SVC}::OrderService.place"),      # typed local variable
            (f"{APP}::App.main", f"{ORDER}::Order.describe"),        # type from wildcard import
            (f"{SVC}::OrderService.place", f"{REPO}::OrderRepository.save"),  # typed field
            (f"{SVC}::OrderService.place", f"{SVC}::OrderService.audit"),     # same-class call
            (f"{SVC}::OrderService.audit", f"{HELP}::Helpers.log"),  # same package, no import
            (f"{ORDER}::Order.format", f"{STATUS}::Status.isFinal"),
            (f"{SVC}::OrderService.place#2", f"{SVC}::OrderService.place"),
        }
        self.assertTrue(expected <= calls, expected - calls)
        # repo.save() goes to the concrete class, not the interface.
        self.assertNotIn((f"{SVC}::OrderService.place", f"{IREPO}::Repository.save"), calls)
        # System.out.println is the JDK, not our code.
        self.assertFalse(any(t.endswith("println") for _, t in calls))


if __name__ == "__main__":
    unittest.main()
