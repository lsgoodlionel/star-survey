/**
 * MJY 轮播图（R02-20）。
 *
 * 给每个已经渲染好的选项行配上一张平台资产图，做成可左右切换的轮播。
 * 只动 DOM：radio 的 name / value / id 一个不动，停用本脚本时选项平铺显示，
 * 作答者照样能选、能交，服务端看到的东西完全一样。
 *
 * 三件与需求验收直接对应的事：
 *   - **切换**：上一张 / 下一张 ＋ 圆点索引，键盘左右键同样可用；
 *   - **加载失败**：图 onerror 时显示替代文本与一行提示，选项本身照常可选——
 *     一张图裂了不该把这个选项从问卷上抹掉；
 *   - **替代文本**：alt 由平台强制要求（网关 422），这里原样落到 img[alt] 上。
 *
 * 图的地址是平台签发的取件地址，带签名与过期时刻（ADR 0019 决定 4）。
 * 本脚本不拼地址、不改地址，只把它放进 img.src。
 */
(function () {
    'use strict';

    var AUTOPLAY_MS = 5000;

    function parseSlides(raw) {
        try {
            var slides = JSON.parse(raw || '[]');
            return Array.isArray(slides) ? slides : [];
        } catch (error) {
            return [];
        }
    }

    /** 选项代码 → 选项行。单选的 radio value 就是选项代码。 */
    function indexRows(list) {
        var index = {};
        Array.prototype.forEach.call(list.querySelectorAll('li'), function (row) {
            var radio = row.querySelector('input[type="radio"]');
            var code = radio ? radio.value : '';
            if (code !== '' && !Object.prototype.hasOwnProperty.call(index, code)) {
                index[code] = row;
            }
        });
        return index;
    }

    function buildFigure(slide) {
        var figure = document.createElement('figure');
        figure.className = 'mjy-carousel-figure';
        var image = document.createElement('img');
        image.className = 'mjy-carousel-image';
        image.alt = slide.alt || '';
        image.loading = 'lazy';
        image.addEventListener('error', function () {
            figure.classList.add('mjy-carousel-broken');
            var notice = document.createElement('p');
            notice.className = 'mjy-carousel-error';
            // 图裂了不影响作答：说明这一项是什么，让人照样能选。
            notice.textContent = '图片加载失败：' + (slide.alt || '');
            figure.appendChild(notice);
        });
        image.src = slide.url;
        figure.appendChild(image);
        return figure;
    }

    function buildDot(index, onSelect) {
        var dot = document.createElement('button');
        dot.type = 'button';
        dot.className = 'mjy-carousel-dot';
        dot.setAttribute('aria-label', '第 ' + (index + 1) + ' 张');
        dot.addEventListener('click', function () {
            onSelect(index);
        });
        return dot;
    }

    function buildArrow(label, step, onStep) {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'mjy-carousel-arrow';
        button.setAttribute('aria-label', label);
        button.textContent = step < 0 ? '‹' : '›';
        button.addEventListener('click', function () {
            onStep(step);
        });
        return button;
    }

    /** 把选项行搬进轮播容器，返回「显示第几张」的函数。 */
    function layout(container, list, slides, rows) {
        var panels = [];
        var track = document.createElement('div');
        track.className = 'mjy-carousel-track';
        slides.forEach(function (slide) {
            var row = rows[slide.code];
            if (!row) {
                return;
            }
            var panel = document.createElement('div');
            panel.className = 'mjy-carousel-panel';
            panel.appendChild(buildFigure(slide));
            panel.appendChild(row);
            track.appendChild(panel);
            panels.push(panel);
        });
        if (panels.length === 0) {
            return null;
        }
        container.insertBefore(track, list);
        return panels;
    }

    function activate(container, panels, autoplay) {
        var current = 0;
        var dots = document.createElement('div');
        dots.className = 'mjy-carousel-dots';

        function show(index) {
            current = (index + panels.length) % panels.length;
            panels.forEach(function (panel, position) {
                panel.hidden = position !== current;
            });
            Array.prototype.forEach.call(dots.children, function (dot, position) {
                dot.setAttribute('aria-current', position === current ? 'true' : 'false');
            });
        }

        function step(delta) {
            stop();
            show(current + delta);
        }

        var timer = null;
        function stop() {
            if (timer !== null) {
                window.clearInterval(timer);
                timer = null;
            }
        }

        var controls = document.createElement('div');
        controls.className = 'mjy-carousel-controls';
        controls.appendChild(buildArrow('上一张', -1, step));
        panels.forEach(function (_panel, index) {
            dots.appendChild(buildDot(index, function (target) {
                stop();
                show(target);
            }));
        });
        controls.appendChild(dots);
        controls.appendChild(buildArrow('下一张', 1, step));
        container.appendChild(controls);

        container.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowLeft') {
                step(-1);
            } else if (event.key === 'ArrowRight') {
                step(1);
            }
        });
        // 作答者一动就停：自动轮播不该把人正在看的那张换走。
        container.addEventListener('change', stop);

        show(0);
        if (autoplay) {
            timer = window.setInterval(function () {
                show(current + 1);
            }, AUTOPLAY_MS);
        }
    }

    function enhance(container) {
        var list = container.querySelector('ul');
        if (!list) {
            return;
        }
        var slides = parseSlides(container.getAttribute('data-mjy-carousel'));
        if (slides.length === 0) {
            return;
        }
        var panels = layout(container, list, slides, indexRows(list));
        if (panels === null) {
            return;
        }
        if (list.querySelectorAll('li').length === 0) {
            list.hidden = true;
        }
        activate(container, panels, container.getAttribute('data-mjy-autoplay') === '1');
    }

    function boot() {
        Array.prototype.forEach.call(document.querySelectorAll('.mjy-carousel'), enhance);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
}());
