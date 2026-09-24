/**
 * MJY 图片 PK 的编辑器（R02-17）。
 *
 * 只负责编辑体验：把每一对渲染成两张图，点哪张就写哪个代码。真正的判定在服务端
 * （plugins/MjyQuestionExtensions/MjyRepeatingTableValidator.php 的枚举列），
 * 因为浏览器端的任何结论都不可信——信封可以被整块替换掉。
 *
 * 配对由平台声明、固定不变；**随机的是每一对里两张图的左右位置**，
 * 而随机的那部分会连同选择一起写进 <配对代码>_shown，事后可追溯到
 * 「这个人是在哪种摆法下做的选择」（位置偏好是成对比较的已知偏倚）。
 * 位置在首次渲染时定一次就不再变，免得作答者返回上一页时看到的摆法又不一样。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;
    var SHOWN_SUFFIX = '_shown';

    function parseJson(raw) {
        try {
            var value = JSON.parse(raw || '');
            return Array.isArray(value) ? value : [];
        } catch (error) {
            return [];
        }
    }

    /** 已存的那一行；没有或读不懂就当空白重来。 */
    function parseRow(raw) {
        try {
            var envelope = JSON.parse(raw || '');
            if (!envelope || envelope.v !== ENVELOPE_VERSION || !Array.isArray(envelope.rows)) {
                return {};
            }
            var row = envelope.rows[0];
            return row && typeof row === 'object' ? row : {};
        } catch (error) {
            return {};
        }
    }

    function MjyImagePk(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-image-pk__raw');
        this.editor = root.querySelector('.mjy-image-pk__editor');
        this.list = root.querySelector('.mjy-image-pk__pairs');
        this.pairs = parseJson(root.getAttribute('data-mjy-pairs'));
        this.items = {};
        parseJson(root.getAttribute('data-mjy-items')).forEach(function (item) {
            this.items[item.code] = item;
        }, this);
    }

    MjyImagePk.prototype.start = function () {
        if (!this.textarea || !this.pairs.length) {
            return; // 配置缺失时退回纯 textarea，作答者不至于被卡住
        }
        this.row = parseRow(this.textarea.value);
        this.textarea.hidden = true;
        this.editor.hidden = false;
        this.pairs.forEach(this.renderPair, this);
        this.write();
    };

    /** 左右位置：已经记过就沿用，第一次渲染才掷一次。 */
    MjyImagePk.prototype.sidesFor = function (pair) {
        var recorded = this.row[pair.code + SHOWN_SUFFIX];
        if (recorded === pair.right) {
            return [pair.right, pair.left];
        }
        if (recorded === pair.left) {
            return [pair.left, pair.right];
        }
        return Math.random() < 0.5 ? [pair.left, pair.right] : [pair.right, pair.left];
    };

    MjyImagePk.prototype.renderPair = function (pair) {
        var sides = this.sidesFor(pair);
        this.row[pair.code + SHOWN_SUFFIX] = sides[0];
        if (typeof this.row[pair.code] !== 'string') {
            this.row[pair.code] = '';
        }
        var line = document.createElement('li');
        line.className = 'mjy-image-pk__pair';
        line.setAttribute('data-mjy-pair', pair.code);
        sides.forEach(function (code) {
            line.appendChild(this.card(pair, code));
        }, this);
        this.list.appendChild(line);
    };

    MjyImagePk.prototype.card = function (pair, code) {
        var item = this.items[code] || { code: code, label: code, image: '' };
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'mjy-image-pk__card';
        button.setAttribute('data-mjy-choice', code);
        button.setAttribute('aria-pressed', this.row[pair.code] === code ? 'true' : 'false');

        var picture = document.createElement('img');
        picture.className = 'mjy-image-pk__image';
        picture.src = String(item.image || '');
        picture.alt = String(item.label || code);
        button.appendChild(picture);

        var caption = document.createElement('span');
        caption.className = 'mjy-image-pk__label';
        caption.textContent = String(item.label || code);
        button.appendChild(caption);

        button.addEventListener('click', this.choose.bind(this, pair, code));
        return button;
    };

    MjyImagePk.prototype.choose = function (pair, code) {
        this.row[pair.code] = code;
        var line = this.list.querySelector('[data-mjy-pair="' + pair.code + '"]');
        Array.prototype.forEach.call(line.querySelectorAll('.mjy-image-pk__card'), function (button) {
            button.setAttribute('aria-pressed', button.getAttribute('data-mjy-choice') === code ? 'true' : 'false');
        });
        this.write();
    };

    /** 编辑器的唯一出口：永远整块重写信封，不做增量拼接。 */
    MjyImagePk.prototype.write = function () {
        this.textarea.value = JSON.stringify({ v: ENVELOPE_VERSION, rows: [this.row] });
    };

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-image-pk]'), function (root) {
            new MjyImagePk(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
