"""Verifica que la evaluación no premie respuestas incorrectas o inventadas."""
import unittest

from benchmark import assess, cases, validate


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases = {c["id"]: c for c in cases(0, None)}

    def test_correct_report_and_incorrect_completed_claim(self):
        case = self.cases["seguimiento"]
        response = {**case["esperado"], "resumen": "CI falló", "recomendaciones": ["Revisar pruebas"]}
        self.assertEqual(validate(response, case["schema"]), [])
        self.assertEqual(assess(case, response)["puntos"], 6)
        response["completadas"] = 6
        self.assertIn("completadas", assess(case, response)["fallos"])

    def test_extra_cards_change_expected_totals(self):
        case = cases(94, ["seguimiento"])[0]
        self.assertEqual(case["esperado"]["total"], 100)
        self.assertEqual(case["esperado"]["avance_porcentaje"], 2)
        self.assertEqual(case["esperado"]["vencidas_ids"], ["T2", "T3"])

    def test_missing_data_cannot_be_approved(self):
        case = self.cases["aprobacion"]
        response = {**case["esperado"], "justificacion": "Faltan requisitos"}
        self.assertEqual(assess(case, response)["puntos"], 4)
        response["decision"] = "recomendar_aprobacion"
        response["sin_evidencia_ids"] = []
        self.assertEqual(assess(case, response)["puntos"], 2)

    def test_duplicate_ids_do_not_count_as_correct(self):
        case = self.cases["aprobacion"]
        response = {**case["esperado"], "sin_evidencia_ids": ["Q4", "Q4"], "justificacion": ""}
        self.assertIn("sin_evidencia_ids", assess(case, response)["fallos"])

    def test_passing_control_must_not_be_rejected(self):
        case = self.cases["aprobacion_correcta"]
        response = {**case["esperado"], "justificacion": "Todas las reglas se cumplen"}
        self.assertEqual(assess(case, response)["puntos"], 4)
        response["decision"] = "solicitar_cambios"
        self.assertIn("decision", assess(case, response)["fallos"])

    def test_code_findings_need_correct_evidence_lines(self):
        case = self.cases["revision_codigo"]
        response = {"hallazgos": [{"categoria": category, "linea": lines[0], "evidencia": "Código",
                                  "correccion": "Corrección"} for category, lines in case["esperado"]["lineas"].items()]}
        self.assertEqual(assess(case, response)["puntos"], 4)
        response["hallazgos"][0]["linea"] = 1
        self.assertIn("inyeccion_sql", assess(case, response)["fallos"])
        response["hallazgos"].append({"categoria": "otros", "linea": 2, "evidencia": "", "correccion": ""})
        self.assertIn("sin_hallazgos_extra", assess(case, response)["fallos"])

    def test_schema_rejects_boolean_numbers_and_extra_fields(self):
        case = self.cases["seguimiento"]
        response = {**case["esperado"], "total": True, "resumen": "", "recomendaciones": [], "extra": 5}
        errors = validate(response, case["schema"])
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
