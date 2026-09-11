"""Testes da logica de classificacao do web_audit.

Sem rede e sem navegador: exercitam exatamente as decisoes que produziram
falso positivo antes deste modulo existir (lazy-load, WAF, encolhimento no
celular) e a regra de que controle falho invalida a rodada.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_audit import (  # noqa: E402
    MOBILE,
    broken_images,
    classify,
    looks_like_bot_challenge,
    shrink_to_fit_px,
    summarize,
)


class TestBotChallenge(unittest.TestCase):
    def test_robot_challenge_screen(self):
        self.assertTrue(looks_like_bot_challenge(202, "Robot Challenge Screen", ""))

    def test_cloudfront_block(self):
        self.assertTrue(
            looks_like_bot_challenge(200, "", "403 ERROR\nThe request could not be satisfied.")
        )

    def test_cloudflare_interstitial(self):
        self.assertTrue(looks_like_bot_challenge(503, "Just a moment...", ""))

    def test_406_from_waf_is_inconclusive(self):
        # 406 apareceu em tres sites que abriam normalmente no navegador.
        self.assertTrue(looks_like_bot_challenge(406, "", ""))

    def test_healthy_page_is_not_a_challenge(self):
        self.assertFalse(looks_like_bot_challenge(200, "Padaria X", "Nosso cardapio"))

    def test_real_404_is_not_a_challenge(self):
        self.assertFalse(looks_like_bot_challenge(404, "Nao encontrado", "pagina sumiu"))


class TestShrinkToFit(unittest.TestCase):
    def test_responsive_page_has_no_shrink(self):
        self.assertEqual(shrink_to_fit_px(390, MOBILE["width"]), 0)

    def test_wider_layout_is_measured(self):
        self.assertEqual(shrink_to_fit_px(560, MOBILE["width"]), 170)

    def test_missing_value_is_zero(self):
        self.assertEqual(shrink_to_fit_px(None, MOBILE["width"]), 0)


class TestBrokenImages(unittest.TestCase):
    def test_lazy_load_placeholder_is_not_broken(self):
        imgs = [{"src": "", "complete": True, "naturalWidth": 0}]
        self.assertEqual(broken_images(imgs), [])

    def test_image_with_src_that_failed_is_broken(self):
        imgs = [{"src": "https://x/a.png", "complete": True, "naturalWidth": 0}]
        self.assertEqual(broken_images(imgs), ["https://x/a.png"])

    def test_loaded_image_is_fine(self):
        imgs = [{"src": "https://x/a.png", "complete": True, "naturalWidth": 800}]
        self.assertEqual(broken_images(imgs), [])

    def test_still_loading_is_not_broken(self):
        imgs = [{"src": "https://x/a.png", "complete": False, "naturalWidth": 0}]
        self.assertEqual(broken_images(imgs), [])


class TestClassify(unittest.TestCase):
    def _view(self, **over):
        base = {
            "viewport": "mobile",
            "status": 200,
            "title": "Loja",
            "bodyText": "conteudo",
            "innerWidth": MOBILE["width"],
            "horizOverflowPx": 0,
            "images": [],
            "tinyTextNodes": 0,
            "textLen": 3000,
            "httpErrors": [],
        }
        base.update(over)
        return base

    def test_clean_page_yields_no_findings(self):
        res = classify(self._view())
        self.assertEqual(res["findings"], [])
        self.assertEqual(res["inconclusive"], [])

    def test_bot_challenge_suppresses_findings(self):
        res = classify(self._view(status=403, textLen=10, innerWidth=1200))
        self.assertEqual(res["findings"], [], "WAF nao pode virar achado")
        self.assertEqual(len(res["inconclusive"]), 1)

    def test_navigation_error_is_inconclusive(self):
        res = classify({"viewport": "desktop", "error": "TimeoutError: ..."})
        self.assertEqual(res["findings"], [])
        self.assertIn("nao foi possivel carregar", res["inconclusive"][0])

    def test_shrink_to_fit_is_a_finding_on_mobile(self):
        res = classify(self._view(innerWidth=560))
        self.assertTrue(any("nao responsivo" in f for f in res["findings"]))

    def test_shrink_not_evaluated_on_desktop(self):
        res = classify(self._view(viewport="desktop", innerWidth=1600))
        self.assertEqual(res["findings"], [])

    def test_thin_page_is_a_finding(self):
        res = classify(self._view(textLen=488))
        self.assertTrue(any("caracteres de texto" in f for f in res["findings"]))

    def test_small_overflow_is_tolerated(self):
        self.assertEqual(classify(self._view(horizOverflowPx=17))["findings"], [])

    def test_large_overflow_is_a_finding(self):
        res = classify(self._view(horizOverflowPx=316))
        self.assertTrue(any("estouro horizontal" in f for f in res["findings"]))

    def test_few_tiny_texts_tolerated(self):
        self.assertEqual(classify(self._view(tinyTextNodes=3))["findings"], [])

    def test_many_tiny_texts_is_a_finding(self):
        res = classify(self._view(tinyTextNodes=8))
        self.assertTrue(any("abaixo de 11px" in f for f in res["findings"]))


class TestSummarize(unittest.TestCase):
    def test_findings_are_tagged_by_viewport(self):
        page = {
            "url": "https://x",
            "contact": {"tel": True},
            "views": [
                {
                    "viewport": "mobile",
                    "status": 200,
                    "title": "",
                    "bodyText": "",
                    "innerWidth": 560,
                    "horizOverflowPx": 0,
                    "images": [],
                    "tinyTextNodes": 0,
                    "textLen": 3000,
                    "httpErrors": [],
                }
            ],
        }
        out = summarize([page])["pages"][0]
        self.assertTrue(out["findings"][0].startswith("[mobile]"))
        self.assertEqual(out["contact"], {"tel": True})
        self.assertFalse(out["needs_confirmation"])

    def _view(self, **over):
        base = {
            "viewport": "mobile",
            "status": 200,
            "title": "",
            "bodyText": "",
            "innerWidth": MOBILE["width"],
            "horizOverflowPx": 0,
            "images": [],
            "tinyTextNodes": 0,
            "textLen": 3000,
            "httpErrors": [],
        }
        base.update(over)
        return base

    def test_block_in_one_viewport_flags_the_other_findings(self):
        # Observado de verdade: desktop levou desafio de bot (202) e mobile
        # passou. O que passou pode ter passado degradado.
        page = {
            "url": "https://x",
            "contact": {},
            "views": [
                self._view(viewport="desktop", status=202, title="Robot Challenge Screen"),
                self._view(viewport="mobile", innerWidth=674),
            ],
        }
        out = summarize([page])["pages"][0]
        self.assertTrue(out["needs_confirmation"])
        self.assertTrue(all("CONFIRMAR" in f for f in out["findings"]))

    def test_clean_page_is_not_flagged(self):
        page = {"url": "https://x", "contact": {}, "views": [self._view()]}
        out = summarize([page])["pages"][0]
        self.assertEqual(out["findings"], [])
        self.assertFalse(out["needs_confirmation"])


if __name__ == "__main__":
    unittest.main()
