#!/usr/bin/env python3
"""Exercise the running UI with real Agent calls; stores public, sanitized demo evidence."""
import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
CODE_PROMPT = "请开发一个无需安装依赖、可离线打开的工时计费计算器网页。输入正常工时、加班工时、时薪和加班倍数，实时显示应付总金额及分项；输入必须非负；使用中文标签、人民币金额和清晰的响应式样式。交付 index.html 和简短的 README.md，实际检查计算逻辑。不要启动后台服务。"
WORK_PROMPT = "请分析上传的 sales.csv 和 brief.md，使用 Python 实际计算每月及各渠道收入、成本、毛利和毛利率，交付 report.md、summary.csv 和可复查的 analysis.py。报告简洁，区分数据事实与推测，明确这是虚构的演示数据。不要启动后台服务。"
CODE_FOLLOWUP = "在现有工时计费网页上增加“导出 CSV”按钮，导出当前输入和计算结果，中文表头并带 UTF-8 BOM。保留已有功能，实际检查后更新 README。"
WORK_FOLLOWUP = "基于刚才的分析，补充一个 30 天的行动计划，包含负责人角色、时间安排和验收指标。不要编造新数据，建议需标注为建议。保存 action-plan.md，并在 report.md 加入链接。"


async def main(args):
    out = ROOT / "docs/evidence"
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    async with httpx.AsyncClient(base_url=args.base_url, trust_env=False, timeout=30) as client:
        async def get(path):
            response = await client.get('/api' + path)
            response.raise_for_status()
            return response.json()

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport={"width": 1440, "height": 960})
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

            async def open_session(sid):
                await page.goto(args.base_url + '/?session=' + sid + '#' + sid, wait_until="domcontentloaded")
                await page.locator('.message.user').first.wait_for()
                await page.wait_for_timeout(400)

            async def submit(prompt):
                await page.locator('#prompt').fill(prompt)
                async with page.expect_response(lambda r: '/messages' in r.url and r.request.method == 'POST') as result:
                    await page.locator('#send-button').click()
                response = await result.value
                assert response.status == 202, await response.text()
                return (await response.json())["id"]

            await page.goto(args.base_url, wait_until="domcontentloaded")
            await page.locator('#model-label').filter(has_text='linext').wait_for()
            if args.existing_code:
                code_sid = args.existing_code
                await open_session(code_sid)
            else:
                await page.locator('#new-session').click()
                await page.wait_for_url(lambda url: bool(urlsplit(url).fragment))
                code_sid = (await page.evaluate('location.hash')).lstrip('#')
                await submit(CODE_PROMPT)
            await page.screenshot(path=str(out / '02-code-running.png'))
            await client.patch('/api/sessions/' + code_sid, json={"title": "工时计费工具"})

            await page.locator('#new-session').click()
            await page.wait_for_url(lambda url: bool(urlsplit(url).fragment) and urlsplit(url).fragment != code_sid)
            work_sid = (await page.evaluate('location.hash')).lstrip('#')
            await page.locator('#file-input').set_input_files([str(ROOT / 'demo/materials/sales.csv'), str(ROOT / 'demo/materials/brief.md')])
            await expect(page.locator('#attachments .attachment-chip')).to_have_count(2)
            await submit(WORK_PROMPT)
            await client.patch('/api/sessions/' + work_sid, json={"title": "季度经营分析"})
            await page.locator('#activity-tab').click()
            await page.screenshot(path=str(out / '03-materials-running.png'))
            print(json.dumps({"code_session": code_sid, "work_session": work_sid}), flush=True)
            (out / 'sessions.json').write_text(json.dumps({"code_session": code_sid, "work_session": work_sid}, indent=2))

            # Closing the page must leave backend execution running.
            await page.close()
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            await open_session(code_sid)
            await page.locator('#activity-tab').click()
            await page.screenshot(path=str(out / '04-reconnected.png'))

            async def wait_done(sid):
                started = time.monotonic()
                previous = None
                while time.monotonic() - started < 1000:
                    data = await get('/sessions/' + sid)
                    run = data['runs'][-1]
                    label = (run['status'], len(data['events']))
                    if label != previous:
                        print(sid[:8], 'status', run['status'], 'events', label[1], flush=True)
                        previous = label
                    if run['status'] not in ('running', 'queued'):
                        if run['status'] != 'completed':
                            raise RuntimeError(f"Real Agent task failed: {run['error']}")
                        await asyncio.sleep(.5)
                        return await get('/sessions/' + sid)
                    await asyncio.sleep(2)
                raise TimeoutError('Demo Agent run timed out')

            code, work = await asyncio.gather(wait_done(code_sid), wait_done(work_sid))
            initial_ids = {code_sid: code['cli_session_id'], work_sid: work['cli_session_id']}
            await open_session(code_sid)
            await page.screenshot(path=str(out / '05-code-completed.png'))
            await submit(CODE_FOLLOWUP)
            await open_session(work_sid)
            await submit(WORK_FOLLOWUP)
            code, work = await asyncio.gather(wait_done(code_sid), wait_done(work_sid))
            assert code['cli_session_id'] == initial_ids[code_sid]
            assert work['cli_session_id'] == initial_ids[work_sid]
            for label, sid, data in [('code', code_sid, code), ('work', work_sid, work)]:
                files = (await get('/sessions/' + sid + '/files'))['files']
                for file in files:
                    if file['source'] != 'output' or file['size'] > 2_000_000:
                        continue
                    target = out / 'generated' / label / file['path']
                    target.parent.mkdir(parents=True, exist_ok=True)
                    response = await client.get('/api/sessions/' + sid + '/files/download', params={'path': file['path']})
                    response.raise_for_status()
                    target.write_bytes(response.content)
                (out / (label + '-transcript.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))
                await open_session(sid)
                await page.screenshot(path=str(out / ('06-' + label + '-followup.png')))
            await page.reload(wait_until='domcontentloaded')
            await expect(page.locator('.message')).to_have_count(4)
            assert await page.locator('.session-item').count() >= 2
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.screenshot(path=str(out / '07-mobile.png'))
            overflow = await page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not overflow, 'Mobile page overflows horizontally'
            results = {"real_agent": True, "cli": "OpenCode 1.2.27", "model": code['runs'][0]['model'],
                       "sessions": {"code": code_sid, "work": work_sid}, "continuation_preserved_cli_session": True,
                       "close_and_reopen": True, "mobile_no_horizontal_overflow": True, "browser_errors": errors,
                       "runs": [r for s in (code, work) for r in s['runs']],
                       "tool_calls": sum(e['kind'] == 'tool' for s in (code, work) for e in s['events'])}
            (out / 'demo-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps(results, ensure_ascii=False), flush=True)
            await context.close()
            await browser.close()
            assert not errors, errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8787')
    parser.add_argument('--existing-code', default='')
    asyncio.run(main(parser.parse_args()))
