"""Optional browser acceptance against an already-running local demo server."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=[
            '--disable-gpu', '--disable-webgl', '--use-gl=disabled', '--disable-accelerated-video-decode'])
        context = browser.new_context(viewport={'width':1440, 'height':1100}, accept_downloads=True)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{args.port}', wait_until='networkidle')
        ready = "!busy && document.getElementById('frame').complete && document.getElementById('frame').naturalWidth > 0"
        page.wait_for_function(ready)
        page.select_option('#level', 'workshop')
        page.wait_for_function(ready)
        page.keyboard.press('ArrowRight')
        page.wait_for_function("document.getElementById('steps').textContent==='01' && !busy")
        page.keyboard.press('ArrowUp')
        page.wait_for_function("document.getElementById('steps').textContent==='02' && !busy")
        assert page.locator('#pushes').inner_text() == '01'
        page.click('#reset')
        page.wait_for_function("document.getElementById('steps').textContent==='00' && !busy")
        page.click('#fixed')
        page.wait_for_function("document.getElementById('badge').textContent==='COMPLETE' && !busy && !playing")
        with page.expect_download() as download:
            page.click('#export')
        download.value.save_as(args.out/'downloaded-run.json')
        assert json.loads((args.out/'downloaded-run.json').read_text())['summary']['success']
        page.click('#new')
        page.wait_for_function("document.getElementById('level').value==='generated' && !busy")
        assert page.request.get(f'http://127.0.0.1:{args.port}/state').json()['initial_solvability'] == 'solvable'
        page.select_option('#level', 'trap')
        page.wait_for_function("document.getElementById('feasibility').textContent==='Scene has no solution' && !busy")
        scene = Path(__file__).resolve().parents[1]/'examples/world/detour.json'
        page.set_input_files('#sceneFile', scene)
        page.wait_for_function("document.getElementById('level').value==='custom' && !busy")
        page.click('#fixed')
        page.wait_for_function("document.getElementById('steps').textContent!=='00' && !playing && !busy")
        assert page.locator('#badge').inner_text() != 'COMPLETE'
        page.click('#reset')
        page.wait_for_function("document.getElementById('steps').textContent==='00' && !busy")
        assert page.locator('#level').input_value() == 'custom'
        page.click('#oracle')
        page.wait_for_function("document.getElementById('badge').textContent==='COMPLETE' && !busy && !playing")
        page.select_option('#level', 'workshop')
        page.wait_for_function("document.getElementById('steps').textContent==='00' && !busy")
        page.click('#rotateLeft')
        page.wait_for_timeout(400)
        page.click('#rotateRight')
        page.wait_for_timeout(400)
        page.screenshot(path=args.out/'desktop.png', full_page=True)
        page.set_viewport_size({'width':390, 'height':844})
        page.screenshot(path=args.out/'mobile.png', full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        (args.out/'result.json').write_text(json.dumps({
            'passed':True, 'checks':['keyboard move', 'push', 'reset', 'fixed-agent completion',
                'JSON download', 'new generated room', 'unsolvable scene', 'structured scene upload', 'agent insufficiency',
                'loaded-scene reset', 'reference replay', 'camera', 'mobile overflow', 'no JS errors'],
            'browser':browser.version, 'gpu_disabled':True}, indent=2)+'\n')
        browser.close()
    print('Browser acceptance passed.')


if __name__ == '__main__':
    main()
