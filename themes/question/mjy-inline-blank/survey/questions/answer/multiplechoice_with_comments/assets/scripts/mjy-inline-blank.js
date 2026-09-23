/**
 * MJY 选项内嵌填空（R02-07）。
 *
 * 把每个选项行的评论输入框挪到勾选框同一行，未勾选时清空并禁用它。
 *
 * 这只是编辑体验。真正的闸门在服务端：
 *  - 没勾选却有填空 → 引擎的 commented_checkbox=checked 把人留在本页；
 *  - 填空超长 → 发布网关编译进 em_validation_q 的长度规则。
 * 禁用一个 input 只是让浏览器不提交它，作答者改掉 disabled 也绕不过上面两条。
 */
(function () {
    'use strict';

    function pair(row) {
        var checkbox = row.querySelector('input[type="checkbox"]');
        var comment = row.querySelector('input[type="text"], textarea');
        return (checkbox && comment) ? { checkbox: checkbox, comment: comment } : null;
    }

    function attach(box) {
        var label = box.getAttribute('data-mjy-blank-label') || '';
        var limit = parseInt(box.getAttribute('data-mjy-blank-max'), 10) || 0;

        Array.prototype.forEach.call(box.querySelectorAll('li'), function (row) {
            var parts = pair(row);
            if (!parts) {
                return;
            }
            row.classList.add('mjy-inline-blank__row');
            parts.comment.classList.add('mjy-inline-blank__blank');
            if (label) {
                parts.comment.setAttribute('placeholder', label);
            }
            if (limit > 0) {
                parts.comment.setAttribute('maxlength', String(limit));
            }

            var sync = function () {
                var checked = parts.checkbox.checked;
                if (!checked && parts.comment.value !== '') {
                    parts.comment.value = '';
                    parts.comment.dispatchEvent(new Event('change', { bubbles: true }));
                }
                parts.comment.disabled = !checked;
            };
            parts.checkbox.addEventListener('change', sync);
            sync();
        });
    }

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-inline-blank]'), attach);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
