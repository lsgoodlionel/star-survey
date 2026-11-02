import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';

afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals(); });

test.each(['/survey/index.php/123456', '/survey/123456'])('initializes cascading controls at %s using the survey form id', (path) => {
  window.history.replaceState({}, '', path);
  document.body.innerHTML = `<form><input name="sid" value="123456">
    <div data-mjy-cascading-select="1" data-mjy-sid="" data-mjy-dictionary="regions" data-mjy-version="v1"
      data-mjy-levels='["地区"]' data-mjy-columns='[{"code":"L1","type":"dict","level":1}]'>
      <div class="mjy-cascading-select__editor" hidden><div class="mjy-cascading-select__levels"></div></div>
      <textarea class="mjy-cascading-select__raw"></textarea></div></form>`;
  const open = vi.fn();
  vi.stubGlobal('XMLHttpRequest', class { open = open; send() {} });
  const script = readFileSync(resolve(process.cwd(), '../../../themes/question/mjy-cascading-select/survey/questions/answer/longfreetext/assets/scripts/mjy-cascading-select.js'), 'utf8');
  window.eval(script);
  document.dispatchEvent(new Event('DOMContentLoaded'));
  expect(document.querySelector('.mjy-cascading-select__editor')).not.toHaveAttribute('hidden');
  expect(open).toHaveBeenCalledWith('GET', expect.stringContaining('sid=123456'), true);
  expect(open).toHaveBeenCalledWith('GET', expect.stringMatching(/^\/survey\/index\.php\/plugins\/direct\?/), true);
});

test('reports an invalid dictionary response instead of showing an empty selection', () => {
  document.body.innerHTML = `<form><input name="sid" value="123456"><div data-mjy-cascading-select="1"
    data-mjy-dictionary="regions" data-mjy-version="v1" data-mjy-levels='["地区"]'
    data-mjy-columns='[{"code":"L1","type":"dict","level":1}]'>
    <div class="mjy-cascading-select__editor" hidden><div class="mjy-cascading-select__levels"></div><p class="mjy-cascading-select__status"></p></div>
    <textarea class="mjy-cascading-select__raw"></textarea></div></form>`;
  vi.stubGlobal('XMLHttpRequest', class {
    readyState = 4; status = 200; responseText = '<html></html>'; onreadystatechange = () => {};
    open() {} send() { this.onreadystatechange(); }
  });
  const script = readFileSync(resolve(process.cwd(), '../../../themes/question/mjy-cascading-select/survey/questions/answer/longfreetext/assets/scripts/mjy-cascading-select.js'), 'utf8');
  window.eval(script);
  document.dispatchEvent(new Event('DOMContentLoaded'));
  expect(document.querySelector('.mjy-cascading-select__status')).toHaveTextContent('选项载入失败');
});
