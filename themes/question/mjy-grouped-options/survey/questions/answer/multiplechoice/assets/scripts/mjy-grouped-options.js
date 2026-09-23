/**
 * MJY 选项分类（R02-04）。
 *
 * 把已经渲染好的选项行按 mjy_option_groups 重排进若干个分组区块。
 * 只移动 DOM：input 的 name / value / id 一个不动，停用本脚本时选项平铺显示，
 * 作答者照样能选、能交，服务端看到的东西完全一样。
 *
 * 两种基础题型的「选项代码」藏在不同地方：
 *   - 单选（listradio）：radio 的 value 就是选项代码；
 *   - 多选（multiplechoice）：checkbox 的 value 恒为 Y，代码只在字段名里，
 *     所以由本主题接管的行模板把它打成 li 的 data-mjy-code。
 * 认不出代码的行（「其他」、不作答项）一律留在原来的列表里，绝不丢。
 *
 * 本文件在 listradio 与 multiplechoice 两个 assets 目录下各有一份，必须逐字节一致
 * （引擎按 <主题>/survey/questions/answer/<基础题型>/assets 发布资产，一个题型一份）。
 * tests/test_question_themes.py 的 GroupedOptionsTemplateTest 钉住这一点。
 */
(function () {
    'use strict';

    function parseGroups(raw) {
        try {
            var groups = JSON.parse(raw || '[]');
            return Array.isArray(groups) ? groups : [];
        } catch (error) {
            return [];
        }
    }

    /** 选项代码 → 选项行。认不出代码的行不进索引。 */
    function indexRows(list) {
        var index = {};
        Array.prototype.forEach.call(list.querySelectorAll('li'), function (row) {
            var code = row.getAttribute('data-mjy-code');
            if (code === null) {
                var radio = row.querySelector('input[type="radio"]');
                code = radio ? radio.value : '';
            }
            if (code !== '' && !Object.prototype.hasOwnProperty.call(index, code)) {
                index[code] = row;
            }
        });
        return index;
    }

    function section(label, collapsible) {
        if (!collapsible) {
            var block = document.createElement('div');
            block.className = 'mjy-grouped-options__group';
            var heading = document.createElement('p');
            heading.className = 'mjy-grouped-options__label';
            heading.textContent = label;
            block.appendChild(heading);
            var plainList = document.createElement('ul');
            plainList.className = 'list-unstyled mjy-grouped-options__list';
            block.appendChild(plainList);
            return { block: block, list: plainList };
        }
        var details = document.createElement('details');
        details.className = 'mjy-grouped-options__group';
        details.open = true;
        var summary = document.createElement('summary');
        summary.className = 'mjy-grouped-options__label';
        summary.textContent = label;
        details.appendChild(summary);
        var list = document.createElement('ul');
        list.className = 'list-unstyled mjy-grouped-options__list';
        details.appendChild(list);
        return { block: details, list: list };
    }

    function arrange(box) {
        var groups = parseGroups(box.getAttribute('data-mjy-option-groups'));
        var source = box.querySelector('ul');
        if (!groups.length || !source) {
            return;
        }
        var rows = indexRows(source);
        var collapsible = box.getAttribute('data-mjy-collapsible') === '1';
        var holder = document.createElement('div');
        holder.className = 'mjy-grouped-options__groups';

        groups.forEach(function (group) {
            var codes = Array.isArray(group.codes) ? group.codes : [];
            var built = section(String(group.label || ''), collapsible);
            codes.forEach(function (code) {
                var row = rows[String(code)];
                if (row) {
                    built.list.appendChild(row);
                }
            });
            if (built.list.children.length) {
                holder.appendChild(built.block);
            }
        });
        // 没有被任何一组认领的行（「其他」项、不作答项）留在原来的 ul 里，绝不丢。
        box.insertBefore(holder, source);
        if (!source.children.length) {
            source.classList.add('mjy-grouped-options__empty');
        }
    }

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-option-groups]'), arrange);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
