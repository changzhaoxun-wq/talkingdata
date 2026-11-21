#!/usr/bin/env python3
"""Record a UI walkthrough of the completed real demo; does not call the model."""
import argparse
import asyncio
import json
import shutil
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs/evidence'


async def main(base):
    ids = json.loads((OUT / 'sessions.json').read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={'width': 1440, 'height': 960},
            record_video_dir='/tmp/orbit-recording', record_video_size={'width': 1440, 'height': 960})
        page = await context.new_page()
        video = page.video
        await page.goto(base, wait_until='domcontentloaded')
        await page.locator('.session-item').first.wait_for()
        await page.wait_for_timeout(3000)

        async def select(sid):
            await page.locator(f'.session-item[data-session-id="{sid}"]').click()
            await page.locator('.message.user').first.wait_for()
            await page.wait_for_timeout(500)

        async def open_file(path):
            await page.locator(f'.file-item[data-file-path="{path}"]').click()
            await page.wait_for_timeout(800)

        await select(ids['code_session'])
        await page.locator('#messages-scroll').evaluate('(node) => node.scrollTop = 0')
        await page.wait_for_timeout(4000)
        await page.locator('#activity-tab').click()
        await page.locator('.activity-event details summary').first.click()
        await page.wait_for_timeout(3500)
        await page.locator('#files-tab').click()
        await open_file('index.html')
        await page.locator('#preview-toggle').click()
        frame = page.frame_locator('#preview-content iframe')
        await frame.locator('#rate').fill('100')
        await page.wait_for_timeout(3000)
        async with page.expect_download():
            await frame.locator('#exportBtn').click()
        await page.wait_for_timeout(2500)
        await page.locator('#file-dialog .close-dialog').click()
        await page.locator('#messages-scroll').evaluate('(node) => node.scrollTop = node.scrollHeight')
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(OUT / '06-code-followup.png'))

        await select(ids['work_session'])
        await page.locator('#messages-scroll').evaluate('(node) => node.scrollTop = 0')
        await page.wait_for_timeout(3500)
        await open_file('report.md')
        await page.wait_for_timeout(4000)
        await page.locator('#preview-content').evaluate('(node) => node.scrollTop = 900')
        await page.wait_for_timeout(2500)
        await page.locator('#file-dialog .close-dialog').click()
        await open_file('summary.csv')
        await page.wait_for_timeout(3000)
        await page.locator('#file-dialog .close-dialog').click()
        await open_file('action-plan.md')
        await page.wait_for_timeout(3000)
        await page.locator('#file-dialog .close-dialog').click()
        await page.reload(wait_until='domcontentloaded')
        await page.locator('.message.user').first.wait_for()
        await page.wait_for_timeout(3000)
        await page.set_viewport_size({'width': 390, 'height': 844})
        await page.wait_for_timeout(1200)
        await page.locator('#mobile-menu').click()
        await page.wait_for_timeout(2500)
        await context.close()
        source = Path(await video.path())
        target = OUT / 'orbit-demo.webm'
        shutil.move(str(source), str(target))
        print('Recorded real application UI walkthrough:', target, target.stat().st_size, 'bytes')
        await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8787')
    asyncio.run(main(parser.parse_args().base_url))
