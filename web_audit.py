#!/usr/bin/env python3
"""Auditoria de site em navegador renderizado -- reprodutivel em sessao nova.

Por que existe
--------------
Conferir site por HTML bruto produz falso positivo em serie: lazy-load parece
imagem quebrada, 406 de WAF parece site fora do ar, timeout isolado parece
lentidao cronica, e largura de layout no celular simplesmente nao da para
inferir sem renderizar. Este modulo renderiza a pagina em Chromium, em duas
viewports, e separa ACHADO de INCONCLUSIVO.

Navegador
---------
Resolvido pelo proprio Playwright (`p.chromium.launch()`), que le
PLAYWRIGHT_BROWSERS_PATH. **Nenhum caminho de binario fica embutido aqui** --
caminho com numero de build muda a cada imagem e quebraria em sessao nova.
Se o navegador nao estiver instalado, o erro e explicito.

Rede
----
Duas estrategias, nesta ordem:

1. `direct`  -- o Chromium fala com a rede por conta propria.
2. `proxied` -- as requisicoes da pagina sao servidas por `requests`, que le
   HTTPS_PROXY e o bundle de CA do ambiente (`trust_env`, ligado por padrao).

A estrategia 2 NAO contorna restricao de rede: ela usa exatamente o mesmo proxy
sancionado do ambiente, apenas por um cliente que sabe falar com ele. Nenhuma
verificacao de TLS e desativada em lugar nenhum.

Como o TLS e reterminado no proxy, este modulo **nao avalia certificado** do
site auditado -- seria o certificado do proxy, nao o do alvo.

Autoteste obrigatorio
---------------------
Antes de confiar em qualquer resultado, `run_audit` audita uma URL de controle.
Se o controle falhar, a rodada inteira volta como INCONCLUSIVA: o problema esta
na ferramenta, nao nos sites.

Uso:
    .venv/bin/python web_audit.py https://exemplo.com [outra...] \
        [--json saida.json] [--shots pasta/]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

CONTROL_URL = "https://example.com"

DESKTOP = {"width": 1366, "height": 900}
MOBILE = {"width": 390, "height": 844}
UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# Telemetria de terceiros: ruido que nao diz nada sobre a qualidade do site.
NOISE_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "connect.facebook",
    "facebook.net",
    "clients2.google.com",
    "hotjar",
    "clarity.ms",
)

# Marcas de desafio de bot / bloqueio de borda. Presenca disto = INCONCLUSIVO.
BOT_CHALLENGE_MARKS = (
    "robot challenge",
    "just a moment",
    "attention required",
    "checking your browser",
    "request blocked",
    "access denied",
    "verifying you are human",
    "enable javascript and cookies",
    "cf-browser-verification",
    "403 error",
    "生成されました",
)

# Status que indicam recusa da borda, nao pagina de cliente quebrada.
EDGE_REFUSAL_STATUS = {202, 401, 403, 405, 406, 429, 503}


# ---------------------------------------------------------------------------
# Logica pura -- testavel sem rede e sem navegador
# ---------------------------------------------------------------------------


def looks_like_bot_challenge(status: int | None, title: str, text: str) -> bool:
    """True quando a resposta cheira a desafio de bot / bloqueio de borda.

    Um status de recusa sozinho ja basta: 403/406/429 numa navegacao de topo
    quase sempre e WAF reagindo ao automatismo, nao o site caido para o cliente.
    """
    blob = f"{title}\n{text}".lower()
    if any(mark in blob for mark in BOT_CHALLENGE_MARKS):
        return True
    return status in EDGE_REFUSAL_STATUS


def shrink_to_fit_px(inner_width: float | None, viewport_width: int) -> int:
    """Quanto a pagina se monta mais larga que a viewport do aparelho.

    Se `window.innerWidth` volta maior que a largura do dispositivo, a pagina
    declarou um layout mais largo e o navegador encolheu tudo para caber --
    texto miudo no celular. Zero quando nao ha encolhimento.
    """
    if not inner_width:
        return 0
    return max(0, int(inner_width) - viewport_width)


def broken_images(images: list[dict]) -> list[str]:
    """Imagens realmente quebradas.

    Imagem sem `src` e placeholder de lazy-load, nao imagem quebrada -- contar
    isso como defeito foi o falso positivo mais comum antes deste modulo.
    """
    out = []
    for img in images:
        src = (img.get("src") or "").strip()
        if not src:
            continue
        if img.get("complete") and not img.get("naturalWidth"):
            out.append(src)
    return out


def classify(view: dict) -> dict:
    """Transforma a coleta bruta de uma viewport em achados e inconclusivos."""
    findings: list[str] = []
    inconclusive: list[str] = []

    if view.get("error"):
        inconclusive.append(f"nao foi possivel carregar: {view['error']}")
        return {"findings": findings, "inconclusive": inconclusive}

    status = view.get("status")
    if looks_like_bot_challenge(status, view.get("title", ""), view.get("bodyText", "")):
        inconclusive.append(
            f"resposta parece desafio de bot/bloqueio de borda (status {status}) -- "
            "nao concluir que o site esta fora do ar"
        )
        return {"findings": findings, "inconclusive": inconclusive}

    if status and status >= 400:
        findings.append(f"pagina responde HTTP {status} ao visitante")

    name = view.get("viewport", "")
    if name == "mobile":
        shrink = shrink_to_fit_px(view.get("innerWidth"), MOBILE["width"])
        if shrink > 0:
            findings.append(
                f"nao responsivo: monta {int(view['innerWidth'])}px de largura num "
                f"aparelho de {MOBILE['width']}px, entao o navegador encolhe a pagina"
            )
        if view.get("tinyTextNodes", 0) >= 5:
            findings.append(
                f"{view['tinyTextNodes']} trechos de texto abaixo de 11px no celular"
            )

    overflow = view.get("horizOverflowPx") or 0
    if overflow > 24:
        findings.append(f"estouro horizontal de {overflow}px (rolagem lateral)")

    quebradas = broken_images(view.get("images", []))
    if quebradas:
        findings.append(
            f"{len(quebradas)} imagem(ns) com src que nao carregaram: "
            + ", ".join(s[:70] for s in quebradas[:3])
        )

    for err in view.get("httpErrors", [])[:4]:
        findings.append(f"recurso da propria pagina falhou: {err}")

    if view.get("textLen", 0) < 600:
        findings.append(
            f"pagina com apenas {view.get('textLen', 0)} caracteres de texto visivel "
            "-- conteudo minimo para o visitante"
        )

    return {"findings": findings, "inconclusive": inconclusive}


def summarize(pages: list[dict]) -> dict:
    """Consolida: achado so vale se aparecer sem contradicao entre viewports."""
    out = []
    for page in pages:
        entry: dict[str, Any] = {"url": page["url"], "findings": [], "inconclusive": []}
        for view in page.get("views", []):
            res = classify(view)
            for f in res["findings"]:
                entry["findings"].append(f"[{view.get('viewport')}] {f}")
            for i in res["inconclusive"]:
                entry["inconclusive"].append(f"[{view.get('viewport')}] {i}")
        # Uma viewport bloqueada e a outra nao significa que a borda do site
        # esta interferindo. O que passou pode ter passado degradado, entao os
        # achados dessa pagina nao valem sozinhos -- exigem confirmacao manual.
        if entry["inconclusive"] and entry["findings"]:
            entry["needs_confirmation"] = True
            entry["findings"] = [
                f + "  (CONFIRMAR: outra viewport foi bloqueada nesta mesma pagina)"
                for f in entry["findings"]
            ]
        else:
            entry["needs_confirmation"] = False

        entry["contact"] = page.get("contact", {})
        out.append(entry)
    return {"pages": out}


# ---------------------------------------------------------------------------
# Coleta no navegador
# ---------------------------------------------------------------------------

PROBE = """() => {
  const de = document.documentElement, b = document.body, vw = window.innerWidth;
  const imgs = [...document.images].map(i => ({
    src: i.currentSrc || i.src || '',
    complete: i.complete,
    naturalWidth: i.naturalWidth,
  }));
  const tiny = [...document.querySelectorAll('body *')].filter(e => {
    const t = (e.textContent || '').trim();
    if (!t || e.children.length) return false;
    const fs = parseFloat(getComputedStyle(e).fontSize);
    return fs > 0 && fs < 11;
  }).length;
  return {
    innerWidth: vw,
    horizOverflowPx: Math.max(de.scrollWidth, b ? b.scrollWidth : 0) - vw,
    viewportMeta: (document.querySelector('meta[name="viewport"]') || {}).content || null,
    images: imgs,
    tinyTextNodes: tiny,
    textLen: (b ? b.innerText : '').trim().length,
    bodyText: (b ? b.innerText : '').trim().slice(0, 400),
    hasTel: !!document.querySelector('a[href^="tel:"]'),
    hasMail: !!document.querySelector('a[href^="mailto:"]'),
    hasWhats: !!document.querySelector('a[href*="wa.me"],a[href*="whatsapp"]'),
    forms: document.querySelectorAll('form').length,
    links: [...document.querySelectorAll('a')]
      .map(a => a.href).filter(Boolean).slice(0, 60),
  };
}"""

SCROLL = """async () => {
  const h = document.body.scrollHeight;
  for (let y = 0; y < h; y += 400) {
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 120));
  }
  window.scrollTo(0, 0);
  await new Promise(r => setTimeout(r, 600));
}"""


def _proxy_fetch(url: str, method: str, headers: dict, body):
    """Busca um recurso pelo proxy sancionado do ambiente.

    `requests` le HTTPS_PROXY e REQUESTS_CA_BUNDLE do ambiente por padrao
    (trust_env). A verificacao de TLS fica LIGADA.
    """
    import requests

    drop = {
        "host",
        "content-length",
        "accept-encoding",
        "connection",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
    }
    clean = {k: v for k, v in headers.items() if k.lower() not in drop}
    return requests.request(
        method, url, headers=clean, data=body, timeout=35, allow_redirects=True
    )


async def _serve_via_proxy(route, request, log):
    url = request.url
    if url.startswith("data:") or any(h in url for h in NOISE_HOSTS):
        await route.abort()
        return
    try:
        resp = await asyncio.to_thread(
            _proxy_fetch, url, request.method, await request.all_headers(),
            request.post_data_buffer,
        )
        headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower()
            not in (
                "content-encoding",
                "content-length",
                "transfer-encoding",
                "content-security-policy",
                "content-security-policy-report-only",
                "x-frame-options",
            )
        }
        if resp.status_code >= 400 and url != request.frame.url:
            log.append(f"{resp.status_code} {url[:100]}")
        await route.fulfill(status=resp.status_code, headers=headers, body=resp.content)
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira abort registrado
        log.append(f"{type(exc).__name__} {url[:100]}")
        try:
            await route.abort()
        except Exception:  # noqa: BLE001
            pass


async def _audit_one(browser, url: str, strategy: str, shots_dir: str | None) -> dict:
    page_result: dict[str, Any] = {"url": url, "strategy": strategy, "views": []}
    contact: dict[str, Any] = {}

    for name, viewport, ua in (
        ("desktop", DESKTOP, UA_DESKTOP),
        ("mobile", MOBILE, UA_MOBILE),
    ):
        ctx = await browser.new_context(
            viewport=viewport,
            user_agent=ua,
            is_mobile=(name == "mobile"),
            has_touch=(name == "mobile"),
            device_scale_factor=2 if name == "mobile" else 1,
        )
        page = await ctx.new_page()
        http_errors: list[str] = []
        console_errors: list[str] = []
        page.on(
            "console",
            lambda m: console_errors.append(m.text[:160]) if m.type == "error" else None,
        )
        if strategy == "proxied":
            await page.route(
                "**/*",
                lambda route, request: asyncio.create_task(
                    _serve_via_proxy(route, request, http_errors)
                ),
            )
        else:
            page.on(
                "response",
                lambda r: http_errors.append(f"{r.status} {r.url[:100]}")
                if r.status >= 400
                else None,
            )

        view: dict[str, Any] = {"viewport": name}
        try:
            resp = await page.goto(url, wait_until="load", timeout=70000)
            view["status"] = resp.status if resp else None
            view["finalUrl"] = page.url
            await page.wait_for_timeout(2000)
            await page.evaluate(SCROLL)
            await page.wait_for_timeout(2000)
            view["title"] = await page.title()
            view.update(await page.evaluate(PROBE))
            if shots_dir:
                os.makedirs(shots_dir, exist_ok=True)
                slug = "".join(c if c.isalnum() else "_" for c in url)[:60]
                await page.screenshot(path=os.path.join(shots_dir, f"{slug}_{name}.png"))
            contact = {
                "tel": view.get("hasTel"),
                "mail": view.get("hasMail"),
                "whatsapp": view.get("hasWhats"),
                "forms": view.get("forms"),
            }
        except Exception as exc:  # noqa: BLE001
            view["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

        view["httpErrors"] = http_errors[:8]
        view["consoleErrors"] = console_errors[:5]
        page_result["views"].append(view)
        await ctx.close()

    page_result["contact"] = contact
    return page_result


async def _launch(playwright):
    """Abre o Chromium resolvido pelo proprio Playwright.

    Sem caminho embutido: quem resolve e o Playwright, lendo
    PLAYWRIGHT_BROWSERS_PATH. Erro aqui e de instalacao, e deve aparecer inteiro.
    """
    try:
        return await playwright.chromium.launch(args=["--no-sandbox"])
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Nao foi possivel abrir o Chromium pelo Playwright.\n"
            f"  {type(exc).__name__}: {exc}\n"
            "No ambiente de nuvem o navegador ja vem instalado e "
            "PLAYWRIGHT_BROWSERS_PATH aponta para ele. Fora dele, rode:\n"
            "  .venv/bin/python -m playwright install chromium"
        ) from None


async def run_audit(
    urls: list[str], shots_dir: str | None = None, control_url: str = CONTROL_URL
) -> dict:
    """Audita as URLs. Faz autoteste de controle antes de confiar no resultado."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await _launch(playwright)
        try:
            strategy = "direct"
            control = await _audit_one(browser, control_url, strategy, None)
            if any(v.get("error") for v in control["views"]):
                strategy = "proxied"
                control = await _audit_one(browser, control_url, strategy, None)

            if any(v.get("error") for v in control["views"]):
                return {
                    "harness_ok": False,
                    "strategy": None,
                    "control_url": control_url,
                    "reason": (
                        "o controle nao carregou em nenhuma estrategia de rede; "
                        "a rodada e INCONCLUSIVA -- o problema esta na ferramenta, "
                        "nao nos sites auditados"
                    ),
                    "control": control,
                    "pages": [],
                }

            pages = [await _audit_one(browser, u, strategy, shots_dir) for u in urls]
        finally:
            await browser.close()

    result = summarize(pages)
    result.update({"harness_ok": True, "strategy": strategy, "control_url": control_url})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("urls", nargs="+", help="URLs a auditar")
    parser.add_argument("--json", dest="json_out", help="grava o resultado neste arquivo")
    parser.add_argument("--shots", dest="shots", help="pasta para os screenshots")
    args = parser.parse_args(argv)

    result = asyncio.run(run_audit(args.urls, shots_dir=args.shots))

    if not result["harness_ok"]:
        print("HARNESS INCONCLUSIVO:", result["reason"], file=sys.stderr)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=1)
        return 2

    print(f"estrategia de rede: {result['strategy']} (controle: {result['control_url']})\n")
    for page in result["pages"]:
        print(page["url"])
        print("  contato na pagina:", page["contact"] or "(nao coletado)")
        if page["findings"]:
            for f in page["findings"]:
                print("  ACHADO:", f)
        else:
            print("  ACHADO: nenhum defeito verificado")
        for i in page["inconclusive"]:
            print("  INCONCLUSIVO:", i)
        print()

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        print(f"json em {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
