/**
 * MJY 矩阵单题作答（R02-14）。
 *
 * 一次只显示 N 行，其余行用 CSS 藏起来——**不是**移除，也不禁用：
 * 隐藏的行照样随表单提交，引擎的必答与选项校验在服务端重算，
 * 所以「没走到那一行」绝不等于「那一行不用填」。
 */
(function () {
    'use strict';

    function dataRows(box) {
        var body = box.querySelector('tbody');
        if (!body) {
            return [];
        }
        return Array.prototype.filter.call(body.rows, function (row) {
            return row.querySelector('input, select') !== null;
        });
    }

    function attach(box) {
        var rows = dataRows(box);
        var perStep = Math.max(1, parseInt(box.getAttribute('data-mjy-rows-per-step'), 10) || 1);
        var bar = box.querySelector('.mjy-matrix-stepper__bar');
        if (rows.length <= perStep || !bar) {
            return;
        }
        var steps = Math.ceil(rows.length / perStep);
        var previous = bar.querySelector('.mjy-matrix-stepper__prev');
        var next = bar.querySelector('.mjy-matrix-stepper__next');
        var progress = bar.querySelector('.mjy-matrix-stepper__progress');
        var showProgress = box.getAttribute('data-mjy-progress') !== '0';
        var step = 0;

        function render() {
            rows.forEach(function (row, index) {
                var visible = Math.floor(index / perStep) === step;
                row.classList.toggle('mjy-matrix-stepper__row--hidden', !visible);
            });
            previous.disabled = step === 0;
            next.disabled = step === steps - 1;
            progress.textContent = showProgress ? (step + 1) + ' / ' + steps : '';
        }

        previous.addEventListener('click', function () {
            step = Math.max(0, step - 1);
            render();
        });
        next.addEventListener('click', function () {
            step = Math.min(steps - 1, step + 1);
            render();
        });

        bar.hidden = false;
        render();
    }

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-stepper]'), attach);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
