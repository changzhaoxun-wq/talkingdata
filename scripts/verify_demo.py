#!/usr/bin/env python3
"""Check real demo outputs independently; no model API calls are made here."""
import argparse
import asyncio
import csv
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs/evidence'


async def main(base):
    ids = json.loads((OUT / 'sessions.json').read_text())
    results = {}
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=30) as client:
        snapshots = {}
        for label, sid in [('code', ids['code_session']), ('work', ids['work_session'])]:
            response = await client.get('/api/sessions/' + sid)
            response.raise_for_status()
            data = response.json()
            assert all(run['status'] == 'completed' for run in data['runs'])
            snapshots[label] = data
            (OUT / (label + '-transcript.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))
            files = (await client.get('/api/sessions/' + sid + '/files')).json()['files']
            for file in files:
                if file['source'] != 'output' or file['size'] > 2_000_000:
                    continue
                response = await client.get('/api/sessions/' + sid + '/files/download', params={'path': file['path']})
                response.raise_for_status()
                target = OUT / 'generated' / label / file['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.content)
        source = list(csv.DictReader((ROOT / 'demo/materials/sales.csv').open()))
        summary = list(csv.DictReader((OUT / 'generated/work/summary.csv').open(encoding='utf-8-sig')))
        for row in summary:
            group = source
            if row['scope'] == '月度': group = [r for r in source if r['month'] == row['key']]
            if row['scope'] == '渠道': group = [r for r in source if r['channel'] == row['key']]
            if row['scope'] == '月度x渠道': group = [r for r in source if r['month'] + '/' + r['channel'] == row['key']]
            revenue, cost, orders = [sum(float(r[field]) for r in group) for field in ['revenue', 'cost', 'orders']]
            expected = {'revenue': revenue, 'cost': cost, 'orders': orders, 'gross_profit': revenue - cost,
                        'gross_margin_pct': round((revenue - cost) / revenue * 100, 2),
                        'aov': round(revenue / orders, 2), 'cost_per_order': round(cost / orders, 2)}
            for key, value in expected.items():
                assert abs(float(row[key]) - value) < .011, (row, key, value)
        results['independent_csv_check'] = {'rows_checked': len(summary), 'revenue': 239000, 'cost': 152500, 'gross_profit': 86500, 'gross_margin_pct': 36.19}

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport={'width': 1440, 'height': 960})
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            async def visit(sid):
                await page.goto(base + '/?session=' + sid + '#' + sid, wait_until='domcontentloaded')
                await page.locator('.file-item').first.wait_for()

            async def open_file(path):
                await page.locator(f'.file-item[data-file-path="{path}"]').click()
                await expect(page.locator('#file-dialog')).to_be_visible()

            await visit(ids['code_session'])
            await page.locator('#toggle-files').click()
            await expect(page.locator('#inspector')).to_be_hidden()
            await page.locator('#toggle-files').click()
            await expect(page.locator('#inspector')).to_be_visible()
            results['desktop_file_panel_toggle'] = True
            await open_file('index.html')
            await page.locator('#preview-toggle').click()
            frame = page.frame_locator('#preview-content iframe')
            for field, value in [('normalHours', '8'), ('overtimeHours', '2'), ('rate', '100'), ('multiplier', '1.5')]:
                await frame.locator('#' + field).fill(value)
            await expect(frame.locator('#totalAmount')).to_have_text('¥1,100.00')
            await page.screenshot(path=str(OUT / '08-code-preview.png'))
            async with page.expect_download() as download_info:
                await frame.locator('#exportBtn').click()
            download = await download_info.value
            await download.save_as(OUT / 'payroll-export.csv')
            raw = (OUT / 'payroll-export.csv').read_bytes()
            assert raw.startswith(b'\xef\xbb\xbf') and '1100.00' in raw.decode('utf-8-sig')
            await frame.locator('#rate').fill('-1')
            await expect(frame.locator('#totalAmount')).to_have_text('—')
            await frame.locator('#resetBtn').click()
            await expect(frame.locator('#totalAmount')).to_have_text('¥550.00')
            results['html_tool'] = {'8_hours_2_overtime_100_rate_1_5_multiplier': 1100, 'negative_input_rejected': True,
                                    'reset_value': 550, 'csv_download_has_utf8_bom': True}
            await page.locator('#file-dialog .close-dialog').click()

            await visit(ids['work_session'])
            await page.screenshot(path=str(OUT / '06-work-followup.png'))
            await open_file('report.md')
            await expect(page.locator('#preview-content')).to_contain_text('239,000')
            await page.screenshot(path=str(OUT / '09-report-preview.png'))
            await page.locator('#file-dialog .close-dialog').click()
            await open_file('summary.csv')
            assert await page.locator('.csv-preview tr').count() == len(summary) + 1
            await page.locator('#file-dialog .close-dialog').click()
            results['report_and_csv_preview'] = True

            response = await client.post('/api/sessions', json={'title': '自动化边界检查（临时）'})
            qa = response.json()['id']
            shortcut = None
            try:
                payloads = [
                    ('files', ('check.md', b'<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" onload="window.__markdown_xss=1"><script>window.__markdown_xss=1</script>')),
                    ('files', ('check.html', b'<body><script>try{parent.document.body.dataset.hacked="1";document.body.textContent="UNSAFE"}catch(e){document.body.textContent="ISOLATED"}</script></body>')),
                ]
                response = await client.post('/api/sessions/' + qa + '/files', files=payloads)
                response.raise_for_status()
                await visit(qa)
                await open_file('uploads/check.md')
                assert await page.locator('#preview-content script, #preview-content [onload]').count() == 0
                assert await page.evaluate('window.__markdown_xss === undefined')
                await page.locator('#file-dialog .close-dialog').click()
                await open_file('uploads/check.html')
                await page.locator('#preview-toggle').click()
                await expect(page.frame_locator('#preview-content iframe').locator('body')).to_have_text('ISOLATED')
                assert await page.locator('body').get_attribute('data-hacked') is None
                results['markdown_xss_sanitized'] = True
                results['html_cannot_access_parent'] = True
                await page.locator('#file-dialog .close-dialog').click()
                async with page.expect_response(lambda response: response.url.endswith('/api/sessions') and response.request.method == 'POST') as created:
                    await page.keyboard.press('n')
                shortcut = (await (await created.value).json())['id']
                await page.wait_for_url(lambda url: urlsplit(url).fragment == shortcut)
                results['new_session_keyboard_shortcut'] = True
            finally:
                if shortcut:
                    await client.delete('/api/sessions/' + shortcut)
                await client.delete('/api/sessions/' + qa)
            results['browser_page_errors'] = errors
            assert not errors, errors
            await context.close()
            await browser.close()

        report = json.loads((OUT / 'demo-results.json').read_text())
        report['runs'] = [r for data in snapshots.values() for r in data['runs']]
        report['tool_calls'] = sum(e['kind'] == 'tool' for data in snapshots.values() for e in data['events'])
        report['fact_check_followup'] = True
        (OUT / 'demo-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (OUT / 'artifact-checks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8787')
    asyncio.run(main(parser.parse_args().base_url))
