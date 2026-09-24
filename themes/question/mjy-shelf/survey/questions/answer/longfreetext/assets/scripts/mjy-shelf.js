/**
 * MJY 货架题的编辑器（R02-18）。
 *
 * 只负责编辑体验：把商品热区按归一化坐标铺在货架图上，点一下放进购物篮，
 * 再点一下拿出来；篮子里每件商品有一个件数输入框。改动后整块重写 textarea。
 *
 * 件数上下限、取货件数上下限、商品是否真的在货架上，在这里都只是提示；
 * 真正的判定在服务端（MjyRepeatingTableValidator 的枚举列、唯一列与整数上下限），
 * 因为浏览器端的任何结论都不可信——信封可以被整块替换掉。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;

    function parseJson(raw) {
        try {
            var value = JSON.parse(raw || '');
            return Array.isArray(value) ? value : [];
        } catch (error) {
            return [];
        }
    }

    function parseRows(raw) {
        try {
            var envelope = JSON.parse(raw || '');
            if (!envelope || envelope.v !== ENVELOPE_VERSION || !Array.isArray(envelope.rows)) {
                return [];
            }
            return envelope.rows;
        } catch (error) {
            return [];
        }
    }

    function MjyShelf(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-shelf__raw');
        this.editor = root.querySelector('.mjy-shelf__editor');
        this.stage = root.querySelector('.mjy-shelf__stage');
        this.photo = root.querySelector('.mjy-shelf__photo');
        this.basket = root.querySelector('.mjy-shelf__basket');
        this.products = parseJson(root.getAttribute('data-mjy-products'));
        this.maxRows = parseInt(root.getAttribute('data-mjy-max-rows'), 10) || 10;
    }

    MjyShelf.prototype.start = function () {
        if (!this.textarea || !this.products.length) {
            return; // 配置缺失时退回纯 textarea，作答者不至于被卡住
        }
        // 已存的作答按商品代码归位；认不出的行交给服务端去报错，编辑器不猜。
        this.picked = {};
        var known = {};
        this.products.forEach(function (item) {
            known[item.code] = true;
        });
        parseRows(this.textarea.value).forEach(function (row) {
            if (row && typeof row === 'object' && known[row.product]) {
                this.picked[row.product] = String(row.qty || '1');
            }
        }, this);

        this.photo.src = this.root.getAttribute('data-mjy-image') || '';
        this.photo.alt = '';
        this.textarea.hidden = true;
        this.editor.hidden = false;
        this.products.forEach(this.renderHotspot, this);
        this.renderBasket();
        this.write();
    };

    MjyShelf.prototype.renderHotspot = function (item) {
        var spot = document.createElement('button');
        spot.type = 'button';
        spot.className = 'mjy-shelf__hotspot';
        spot.setAttribute('data-mjy-product', item.code);
        spot.setAttribute('aria-label', String(item.label || item.code));
        spot.style.left = (item.x * 100) + '%';
        spot.style.top = (item.y * 100) + '%';
        spot.style.width = (item.w * 100) + '%';
        spot.style.height = (item.h * 100) + '%';
        spot.addEventListener('click', this.toggle.bind(this, item));
        this.stage.appendChild(spot);
        this.markHotspot(item.code);
    };

    MjyShelf.prototype.markHotspot = function (code) {
        var spot = this.stage.querySelector('[data-mjy-product="' + code + '"]');
        if (spot) {
            spot.setAttribute('aria-pressed', this.picked[code] ? 'true' : 'false');
        }
    };

    MjyShelf.prototype.toggle = function (item) {
        if (this.picked[item.code]) {
            delete this.picked[item.code];
        } else if (Object.keys(this.picked).length < this.maxRows) {
            this.picked[item.code] = '1';
        }
        this.markHotspot(item.code);
        this.renderBasket();
        this.write();
    };

    /** 购物篮按货架声明的顺序排，与写进信封的行序一致。 */
    MjyShelf.prototype.inBasket = function () {
        return this.products.filter(function (item) {
            return Object.prototype.hasOwnProperty.call(this.picked, item.code);
        }, this);
    };

    MjyShelf.prototype.renderBasket = function () {
        this.basket.textContent = '';
        this.inBasket().forEach(function (item) {
            var line = document.createElement('li');
            line.className = 'mjy-shelf__line';

            var label = document.createElement('span');
            label.className = 'mjy-shelf__name';
            label.textContent = String(item.label || item.code);
            line.appendChild(label);

            var field = document.createElement('input');
            field.type = 'number';
            field.min = '1';
            field.className = 'form-control mjy-shelf__qty';
            field.value = this.picked[item.code];
            field.setAttribute('aria-label', String(item.label || item.code) + ' 件数');
            field.addEventListener('input', function () {
                this.picked[item.code] = field.value;
                this.write();
            }.bind(this));
            line.appendChild(field);

            this.basket.appendChild(line);
        }, this);
    };

    /** 编辑器的唯一出口：永远整块重写信封，不做增量拼接。 */
    MjyShelf.prototype.write = function () {
        var rows = this.inBasket().map(function (item) {
            return { product: item.code, qty: this.picked[item.code] };
        }, this);
        this.textarea.value = JSON.stringify({ v: ENVELOPE_VERSION, rows: rows });
    };

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-shelf]'), function (root) {
            new MjyShelf(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
