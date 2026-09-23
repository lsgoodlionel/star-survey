/**
 * MJY 折叠栏目（R02-43）。
 *
 * 把折叠标题之后、下一个折叠标题之前的所有题目移进一个 <details> 里。
 * 纯展示：不碰任何 input 的 name 与 value，停用本脚本问卷照样可以填、可以交。
 */
(function () {
    'use strict';

    function headOf(marker) {
        return marker.closest('.question-container') || marker.parentElement;
    }

    function build(marker) {
        var head = headOf(marker);
        if (!head || head.getAttribute('data-mjy-collapsible-done') === '1') {
            return;
        }
        head.setAttribute('data-mjy-collapsible-done', '1');

        var details = document.createElement('details');
        details.className = 'mjy-collapsible__section';
        details.open = marker.getAttribute('data-mjy-collapsed') !== '1';

        var summary = document.createElement('summary');
        summary.className = 'mjy-collapsible__summary';
        // textContent：文案来自题目属性，绝不当 HTML 解释。
        summary.textContent = marker.getAttribute('data-mjy-summary') || '';
        details.appendChild(summary);

        var body = document.createElement('div');
        body.className = 'mjy-collapsible__body';
        details.appendChild(body);

        head.parentNode.insertBefore(details, head.nextSibling);
        head.classList.add('mjy-collapsible__head');

        var node = details.nextSibling;
        while (node) {
            var next = node.nextSibling;
            if (node.nodeType === 1 && node.querySelector('[data-mjy-collapsible]')) {
                break;
            }
            body.appendChild(node);
            node = next;
        }
    }

    function start() {
        var markers = document.querySelectorAll('[data-mjy-collapsible]');
        // 倒序：先处理靠后的分段，移动节点时不会打乱还没处理的分段。
        for (var index = markers.length - 1; index >= 0; index--) {
            build(markers[index]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
