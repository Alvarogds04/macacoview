"""Tests de la lógica de la tarjeta de cambio Abito ⇄ Gufo.

Todo vive en ``pc_ai_monitor.blocks.residente_logica``: parseo de lo que
informa ``local-ai-models resident``, el texto de confirmación y la
traducción de los códigos de salida de ``local-ai-models switch``
(0 ok, 10 rechazado, 1 falló, 2 motor desconocido).
No se construye ningún widget ni se lanza ningún subproceso.
"""

import unittest

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

from pc_ai_monitor.blocks.residente_logica import (
    confirm_text,
    parse_resident,
    switch_outcome,
)

RESIDENT_OK = '{"resident": "gufo", "abito": {"healthy": false}, "gufo": {"context": 262144}}'


class ParseResidentTests(unittest.TestCase):
    def test_valid_payload_returns_the_dict(self):
        info = parse_resident(RESIDENT_OK)

        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["resident"], "gufo")

    def test_invalid_json_returns_none(self):
        self.assertIsNone(parse_resident("no soy json"))

    def test_empty_output_returns_none(self):
        self.assertIsNone(parse_resident(""))

    def test_null_resident_returns_none(self):
        self.assertIsNone(parse_resident('{"resident": null}'))

    def test_non_object_json_returns_none(self):
        self.assertIsNone(parse_resident('["resident"]'))


class ConfirmTextTests(unittest.TestCase):
    def test_confirming_gufo_warns_about_abito_shutdown_and_load_time(self):
        title, body = confirm_text("gufo")

        self.assertEqual(title, "¿Cambiar a Gufo?")
        self.assertIn("Se apaga Abito", body)
        self.assertIn("~90 s", body)
        self.assertIn("No pueden convivir los dos en memoria", body)

    def test_confirming_abito_warns_about_cutting_in_flight_requests(self):
        title, body = confirm_text("abito")

        self.assertEqual(title, "¿Cambiar a Abito?")
        self.assertIn("Se apaga Gufo", body)
        self.assertIn("cualquier petición en curso se corta", body)
        self.assertIn("~30 s", body)


class SwitchOutcomeTests(unittest.TestCase):
    def test_exit_zero_reports_the_new_resident(self):
        ok, message = switch_outcome(0, "", "", "gufo")

        self.assertTrue(ok)
        self.assertIn("Gufo es ahora el motor residente", message)

    def test_exit_zero_with_already_reports_it_was_resident(self):
        ok, message = switch_outcome(0, "", '{"result": "ALREADY"}', "abito")

        self.assertTrue(ok)
        self.assertIn("Abito ya era el motor residente", message)

    def test_exit_ten_reports_the_refusal_with_the_stderr_reason(self):
        ok, message = switch_outcome(10, "Abito está ocupado respondiendo\n", "", "gufo")

        self.assertFalse(ok)
        self.assertIn("No se cambió a Gufo", message)
        self.assertIn("Abito está ocupado respondiendo", message)

    def test_exit_ten_without_stderr_uses_the_generic_reason(self):
        ok, message = switch_outcome(10, "", "", "abito")

        self.assertFalse(ok)
        self.assertIn("el administrador lo rechazó", message)

    def test_exit_one_reports_the_failure_with_the_detail(self):
        ok, message = switch_outcome(1, "el volumen no respondió", "", "gufo")

        self.assertFalse(ok)
        self.assertIn("El cambio a Gufo falló (código 1)", message)
        self.assertIn("el volumen no respondió", message)

    def test_exit_one_without_stderr_says_there_is_no_detail(self):
        ok, message = switch_outcome(1, "", "", "abito")

        self.assertFalse(ok)
        self.assertIn("sin detalle", message)

    def test_exit_two_reports_the_unknown_engine(self):
        ok, message = switch_outcome(2, "", "", "gufo")

        self.assertFalse(ok)
        self.assertIn("no reconoce el motor Gufo", message)

    def test_missing_cli_is_reported(self):
        ok, message = switch_outcome(None, "", "", "gufo", failure="missing")

        self.assertFalse(ok)
        self.assertIn("No se encontró local-ai-models", message)

    def test_timeout_does_not_assume_the_switch_happened(self):
        ok, message = switch_outcome(None, "", "", "abito", failure="timeout")

        self.assertFalse(ok)
        self.assertIn("agotó el tiempo de espera", message)


if __name__ == "__main__":
    unittest.main()
