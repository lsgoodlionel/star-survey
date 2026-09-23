/**
 * MJY 热力图选区（R02-19）。
 *
 * 在底图上点选，把归一化坐标写回 textarea：{"v":1,"rows":[{"x":"0.5000","y":"0.2500"}]}。
 * 浏览器端的上限只是提示；点数与范围都由插件在服务端重算
 * （MjyRepeatingTableValidator，列定义 x/y 的 min=0 max=1）。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;
    var PRECISION = 4;

    function parsePoints(raw) {
        try {
            var envelope = JSON.parse(raw);
            if (!envelope || envelope.v !== ENVELOPE_VERSION || !Array.isArray(envelope.rows)) {
                return [];
            }
            return envelope.rows
                .map(function (row) {
                    return { x: Number(row && row.x), y: Number(row && row.y) };
                })
                .filter(function (point) {
                    return isFinite(point.x) && isFinite(point.y);
                });
        } catch (error) {
            return [];
        }
    }

    function serialise(points) {
        return JSON.stringify({
            v: ENVELOPE_VERSION,
            rows: points.map(function (point) {
                return { x: point.x.toFixed(PRECISION), y: point.y.toFixed(PRECISION) };
            }),
        });
    }

    function clamp(value) {
        return Math.min(1, Math.max(0, value));
    }

    function attach(box) {
        var raw = box.querySelector('.mjy-heatmap__raw');
        var editor = box.querySelector('.mjy-heatmap__editor');
        var image = box.querySelector('.mjy-heatmap__image');
        var layer = box.querySelector('.mjy-heatmap__points');
        var count = box.querySelector('.mjy-heatmap__count');
        var clear = box.querySelector('.mjy-heatmap__clear');
        var source = box.getAttribute('data-mjy-image') || '';
        if (!raw || !editor || !image || !layer || !source) {
            return;  // 配置不全时留着 textarea，作答者仍能提交。
        }

        var most = Math.max(1, parseInt(box.getAttribute('data-mjy-max-points'), 10) || 1);
        var points = parsePoints(raw.value);

        function render() {
            layer.textContent = '';
            points.forEach(function (point, index) {
                var dot = document.createElement('button');
                dot.type = 'button';
                dot.className = 'mjy-heatmap__point';
                dot.style.left = (point.x * 100) + '%';
                dot.style.top = (point.y * 100) + '%';
                dot.textContent = String(index + 1);
                dot.addEventListener('click', function (event) {
                    event.stopPropagation();
                    points.splice(index, 1);
                    commit();
                });
                layer.appendChild(dot);
            });
            count.textContent = points.length + ' / ' + most;
        }

        function commit() {
            raw.value = serialise(points);
            raw.dispatchEvent(new Event('change', { bubbles: true }));
            render();
        }

        image.addEventListener('load', function () {
            editor.hidden = false;
            raw.classList.add('mjy-heatmap__raw--hidden');
            render();
        });
        image.addEventListener('error', function () {
            // 底图加载不出来就退回原始 textarea，而不是留下一个点不了的空白区。
            editor.hidden = true;
            raw.classList.remove('mjy-heatmap__raw--hidden');
        });
        image.src = source;

        layer.addEventListener('click', function (event) {
            if (points.length >= most) {
                return;
            }
            var box2 = layer.getBoundingClientRect();
            if (!box2.width || !box2.height) {
                return;
            }
            points.push({
                x: clamp((event.clientX - box2.left) / box2.width),
                y: clamp((event.clientY - box2.top) / box2.height),
            });
            commit();
        });

        clear.addEventListener('click', function () {
            points = [];
            commit();
        });
    }

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-heatmap]'), attach);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
