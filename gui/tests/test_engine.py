"""Tests de la lógica de la sección "Motor residente" del panel Motores.

Todo vive en ``pc_ai_monitor.blocks.engine_logica``: parseo del estado que
informa ``local-ai-models engine status --json``, la regla que habilita el
botón de actualización, el texto de confirmación y los mensajes de fallo.
No se construye ningún widget.
"""

import json
import unittest

from pc_ai_monitor.blocks.engine_logica import (
    CONFIRMATION_TEXT,
    MISSING_MESSAGE,
    CliResult,
    EngineStatus,
    admission_text,
    admission_warning,
    display_rows,
    inventory_text,
    parse_engine_status,
    parse_update_result,
    rollback_hint,
    update_enabled,
)


def _result(stdout="", returncode=0, stderr="", failure=None):
    return CliResult(returncode=returncode, stdout=stdout, stderr=stderr, failure=failure)


STATUS_OK = json.dumps(
    {
        "component": "gufo",
        "running": {
            "tag": "0.28.1",
            "image_id": "sha256:abc123",
            "version_label": "0.28.1",
            "revision_label": "fec4759",
            "context": 262144,
            "port": 11008,
            "serving": True,
        },
        "available": {
            "tag": "0.29.0",
            "source": "ghcr.io/gufo-org/toolboxes/gufo-runtime",
            "checked_at": "2026-11-03T10:00:00Z",
        },
        "updatable": True,
        "reason": "",
        "inventory": [
            {"component": "pesos del modelo", "value": "UD-Q4_K_XL", "updatable": False},
            {"component": "cabeza MTP", "value": "shared-Q8_0", "updatable": False},
            {"component": "gufo_ctl.py", "value": "rev fec4759", "updatable": False},
        ],
    }
)

STATUS_REGISTRY_DOWN = json.dumps(
    {
        "component": "gufo",
        "running": {
            "tag": "0.28.1",
            "version_label": "0.28.1",
            "revision_label": "fec4759",
            "context": 262144,
            "port": 11008,
            "serving": True,
        },
        "available": {"error": "el registro no respondió"},
        "updatable": False,
        "reason": "no se pudo consultar el registro",
        "inventory": [],
    }
)


class ParseStatusTests(unittest.TestCase):
    def test_parses_a_status_payload_into_display_rows(self):
        status = parse_engine_status(_result(stdout=STATUS_OK))

        self.assertEqual(status.component, "gufo")
        self.assertTrue(status.updatable)
        rows = dict(display_rows(status))
        self.assertEqual(rows["imagen"], "0.28.1")
        self.assertEqual(rows["versión"], "0.28.1")
        self.assertEqual(rows["revisión"], "fec4759")
        self.assertEqual(rows["contexto"], "262144")
        self.assertEqual(rows["puerto"], "11008")
        self.assertEqual(rows["sirviendo"], "sí")
        self.assertEqual(rows["última versión"], "0.29.0")

    def test_parses_a_full_admission_object(self):
        payload = json.loads(STATUS_OK)
        payload["admission"] = {
            "required_gib": 94.9,
            "footprint_gib": 86.9,
            "footprint_source": "anchor",
            "available_gib": 76.97,
            "missing_gib": 17.93,
            "satisfies": False,
            "context": 131072,
        }
        status = parse_engine_status(_result(stdout=json.dumps(payload)))

        admission = status.admission
        assert admission is not None
        self.assertEqual(admission.required_gib, 94.9)
        self.assertEqual(admission.footprint_gib, 86.9)
        self.assertEqual(admission.footprint_source, "anchor")
        self.assertEqual(admission.available_gib, 76.97)
        self.assertEqual(admission.missing_gib, 17.93)
        self.assertFalse(admission.satisfies)
        self.assertEqual(admission.context, 131072)

    def test_null_admission_is_not_an_error_and_reads_unknown(self):
        payload = json.loads(STATUS_OK)
        payload["admission"] = None
        status = parse_engine_status(_result(stdout=json.dumps(payload)))

        self.assertIsNone(status.admission)
        self.assertEqual(status.error, "")
        self.assertEqual(dict(display_rows(status))["admisión"], "desconocido")

    def test_missing_admission_key_reads_unknown(self):
        status = parse_engine_status(_result(stdout=STATUS_OK))

        self.assertIsNone(status.admission)
        self.assertEqual(status.error, "")
        self.assertEqual(dict(display_rows(status))["admisión"], "desconocido")

    def test_admission_with_integers_parses_as_floats(self):
        payload = json.loads(STATUS_OK)
        payload["admission"] = {
            "required_gib": 95,
            "footprint_gib": 87,
            "available_gib": 77,
            "missing_gib": 18,
            "satisfies": False,
            "context": 131072,
        }
        status = parse_engine_status(_result(stdout=json.dumps(payload)))

        admission = status.admission
        assert admission is not None
        self.assertEqual(admission.required_gib, 95.0)
        self.assertEqual(admission.footprint_gib, 87.0)
        self.assertEqual(admission.available_gib, 77.0)
        self.assertEqual(admission.missing_gib, 18.0)

    def test_inventory_is_read_only_and_not_updatable_here(self):
        status = parse_engine_status(_result(stdout=STATUS_OK))

        self.assertEqual(len(status.inventory), 3)
        self.assertTrue(all(not row.updatable for row in status.inventory))
        text = inventory_text(status)
        for component in ("pesos del modelo", "cabeza MTP", "gufo_ctl.py"):
            self.assertIn(component, text)
        self.assertIn("no se actualiza acá", text)

    def test_registry_error_is_shown_and_blocks_the_update(self):
        status = parse_engine_status(_result(stdout=STATUS_REGISTRY_DOWN))

        self.assertFalse(status.updatable)
        self.assertEqual(status.available_error, "el registro no respondió")
        rows = dict(display_rows(status))
        self.assertIn("no se pudo consultar", rows["última versión"])
        self.assertIn("el registro no respondió", rows["última versión"])
        self.assertFalse(update_enabled(status))

    def test_engine_down_is_reported_without_rows_crashing(self):
        payload = json.dumps(
            {"component": "gufo", "running": {}, "available": {}, "updatable": False,
             "reason": "el motor está apagado", "inventory": []}
        )
        status = parse_engine_status(_result(stdout=payload))

        self.assertFalse(status.serving)
        rows = dict(display_rows(status))
        self.assertEqual(rows["sirviendo"], "no")
        self.assertEqual(rows["imagen"], "desconocida")

    def test_missing_cli_reports_plainly(self):
        status = parse_engine_status(_result(failure="missing"))

        self.assertTrue(status.error)
        self.assertEqual(status.error, MISSING_MESSAGE)
        self.assertEqual(display_rows(status), [])
        self.assertFalse(update_enabled(status))

    def test_nonzero_exit_reports_the_code_and_detail(self):
        status = parse_engine_status(_result(returncode=3, stderr="algo explotó"))

        self.assertIn("código 3", status.error)
        self.assertIn("algo explotó", status.error)
        self.assertFalse(update_enabled(status))

    def test_invalid_json_is_reported(self):
        status = parse_engine_status(_result(stdout="no soy json"))

        self.assertIn("JSON inválido", status.error)
        self.assertFalse(update_enabled(status))


class AdmissionTextTests(unittest.TestCase):
    def test_satisfied_row_says_yes_with_the_controller_numbers(self):
        payload = json.loads(STATUS_OK)
        payload["admission"] = {
            "required_gib": 94.9,
            "available_gib": 96.3,
            "missing_gib": 0.0,
            "satisfies": True,
        }
        status = parse_engine_status(_result(stdout=json.dumps(payload)))

        rows = dict(display_rows(status))
        self.assertEqual(
            rows["admisión"], "sí (necesita 94.9 GiB, hay 96.3 GiB)"
        )
        self.assertEqual(admission_warning(status.admission), "")

    def test_unsatisfied_row_reports_the_missing_gib_rounded(self):
        payload = json.loads(STATUS_OK)
        payload["admission"] = {
            "required_gib": 94.9,
            "available_gib": 76.97,
            "missing_gib": 17.93,
            "satisfies": False,
        }
        status = parse_engine_status(_result(stdout=json.dumps(payload)))

        rows = dict(display_rows(status))
        self.assertEqual(
            rows["admisión"],
            "no: faltan 17.9 GiB (necesita 94.9, hay 77.0)",
        )

    def test_unknown_admission_row_without_a_dictamen(self):
        self.assertEqual(admission_text(None), "desconocido")
        self.assertEqual(admission_warning(None), "")

    def test_warning_appears_only_when_the_machine_cannot_host(self):
        def _status(satisfies):
            payload = json.loads(STATUS_OK)
            payload["admission"] = {
                "required_gib": 94.9,
                "available_gib": 96.3 if satisfies else 76.97,
                "missing_gib": 0.0 if satisfies else 17.93,
                "satisfies": satisfies,
            }
            return parse_engine_status(_result(stdout=json.dumps(payload)))

        warning = admission_warning(_status(False).admission)
        self.assertIn("se va a negar a arrancar", warning)
        self.assertIn("falta de memoria", warning)
        self.assertIn("17.9", warning)
        # Actualizar y arrancar son cosas distintas: nunca se dice que no
        # se pueda actualizar.
        self.assertNotIn("no se puede actualizar", warning)
        self.assertEqual(admission_warning(_status(True).admission), "")

    def test_update_button_rule_is_unchanged_by_admission(self):
        def _status(satisfies):
            payload = json.loads(STATUS_OK)
            payload["admission"] = {
                "required_gib": 94.9,
                "available_gib": 96.3 if satisfies else 76.97,
                "missing_gib": 0.0 if satisfies else 17.93,
                "satisfies": satisfies,
            }
            return parse_engine_status(_result(stdout=json.dumps(payload)))

        # El botón sigue dependiendo solo de ``updatable``: arrancar mal no
        # lo bloquea y arrancar bien no lo habilita si no hay actualización.
        self.assertTrue(update_enabled(_status(False)))
        self.assertTrue(update_enabled(_status(True)))

        payload = json.loads(STATUS_REGISTRY_DOWN)
        payload["admission"] = {
            "required_gib": 94.9,
            "available_gib": 76.97,
            "missing_gib": 17.93,
            "satisfies": False,
        }
        self.assertFalse(
            update_enabled(parse_engine_status(_result(stdout=json.dumps(payload))))
        )


class UpdateButtonRuleTests(unittest.TestCase):
    def test_enabled_only_when_updatable_and_status_is_known(self):
        cases = [
            (parse_engine_status(_result(stdout=STATUS_OK)), True),
            (parse_engine_status(_result(stdout=STATUS_REGISTRY_DOWN)), False),
            (parse_engine_status(_result(failure="missing")), False),
            (EngineStatus(), False),
        ]
        for status, expected in cases:
            self.assertEqual(update_enabled(status), expected, status)


class ConfirmationTextTests(unittest.TestCase):
    def test_confirmation_warns_about_the_restart(self):
        self.assertIn("reinicia", CONFIRMATION_TEXT)

    def test_confirmation_warns_about_the_model_downtime(self):
        self.assertIn("85 segundos", CONFIRMATION_TEXT)
        self.assertIn("2 minutos", CONFIRMATION_TEXT)

    def test_confirmation_warns_about_in_flight_requests(self):
        self.assertIn("solicitud en curso", CONFIRMATION_TEXT)

    def test_confirmation_says_rollback_stays_installed(self):
        self.assertIn("versión anterior", CONFIRMATION_TEXT)


class UpdateResultTests(unittest.TestCase):
    def test_success_reports_from_and_to(self):
        result = _result(stdout=json.dumps({"from": "0.28.1", "to": "0.29.0"}))

        message = parse_update_result(result)

        self.assertIn("actualizado y verificado", message)
        self.assertIn("0.28.1 → 0.29.0", message)

    def test_success_without_payload_still_reports_verified(self):
        message = parse_update_result(_result(stdout=""))

        self.assertIn("actualizado y verificado", message)

    def test_failure_shows_the_cli_error_and_the_rollback(self):
        result = _result(
            returncode=1, stderr="/health no respondió en 90 s"
        )

        message = parse_update_result(result)

        self.assertIn("falló", message)
        self.assertIn("versión anterior", message)
        self.assertIn("/health no respondió en 90 s", message)

    def test_confirmation_required_is_reported(self):
        message = parse_update_result(_result(returncode=10))

        self.assertIn("confirmación", message)

    def test_timeout_does_not_assume_success(self):
        message = parse_update_result(_result(failure="timeout"))

        self.assertIn("tiempo de espera", message)
        self.assertNotIn("actualizado y verificado", message)

    def test_missing_cli_is_reported(self):
        self.assertEqual(parse_update_result(_result(failure="missing")), MISSING_MESSAGE)


class RollbackHintTests(unittest.TestCase):
    def test_hint_names_the_exact_command(self):
        hint = rollback_hint("0.28.1")

        self.assertIn("0.28.1", hint)
        self.assertIn("engine update --to 0.28.1 --yes", hint)


if __name__ == "__main__":
    unittest.main()
