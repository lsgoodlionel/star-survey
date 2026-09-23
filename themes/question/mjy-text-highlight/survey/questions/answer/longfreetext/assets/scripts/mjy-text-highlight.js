/**
 * MJY 文字点睛的编辑器（R02-22）。
 *
 * 只负责编辑体验：按声明好的偏移把原文切成可点的片段，点一下加进标记清单，
 * 再点一下取消；清单里每处标记有一个标记下拉。改动后整块重写 textarea。
 *
 * 片段是否真的在原文里、同一段有没有标两次、标记是不是声明过的那几个，
 * 在这里都只是提示；真正的判定在服务端（MjyRepeatingTableValidator 的枚举列与
 * 唯一列），因为浏览器端的任何结论都不可信——信封可以被整块替换掉。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;
    var SEGMENT_COLUMN = 'segment';
    var TAG_COLUMN = 'tag';

    function parseJson(raw, fallback) {
        try {
            var value = JSON.parse(raw || '');
            return Array.isArray(value) ? value : fallback;
        } catch (error) {
            return fallback;
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

    /** 标记的可选值就是 tag 列的取值集合——列定义已经带着它，不再单独下发一份。 */
    function tagOptions(columns) {
        for (var index = 0; index < columns.length; index += 1) {
            if (columns[index] && columns[index].code === TAG_COLUMN) {
                return Array.isArray(columns[index].options) ? columns[index].options : [];
            }
        }
        return [];
    }

    function MjyTextHighlight(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-text-highlight__raw');
        this.editor = root.querySelector('.mjy-text-highlight__editor');
        this.passage = root.querySelector('.mjy-text-highlight__passage');
        this.marks = root.querySelector('.mjy-text-highlight__marks');
        this.text = root.getAttribute('data-mjy-text') || '';
        this.segments = parseJson(root.getAttribute('data-mjy-segments'), []);
        this.tags = tagOptions(parseJson(root.getAttribute('data-mjy-columns'), []));
        this.maxRows = parseInt(root.getAttribute('data-mjy-max-rows'), 10) || 10;
    }

    MjyTextHighlight.prototype.start = function () {
        if (!this.textarea || !this.segments.length || !this.tags.length) {
            return; // 配置缺失时退回纯 textarea，作答者不至于被卡住
        }
        // 已存的作答按片段代码归位；认不出的行交给服务端去报错，编辑器不猜。
        this.picked = {};
        var known = {};
        this.segments.forEach(function (item) {
            known[item.code] = true;
        });
        parseRows(this.textarea.value).forEach(function (row) {
            if (row && typeof row === 'object' && known[row.segment]) {
                this.picked[row.segment] = String(row.tag || this.tags[0].code);
            }
        }, this);

        this.textarea.hidden = true;
        this.editor.hidden = false;
        this.renderPassage();
        this.renderMarks();
        this.write();
    };

    /** 按偏移把原文切成「片段」与「片段之间的普通文字」两种块。 */
    MjyTextHighlight.prototype.renderPassage = function () {
        this.passage.textContent = '';
        var cursor = 0;
        this.segments.forEach(function (item) {
            var start = Number(item.start);
            var length = Number(item.length);
            if (start > cursor) {
                this.passage.appendChild(document.createTextNode(this.text.slice(cursor, start)));
            }
            this.passage.appendChild(this.renderSpot(item, this.text.substr(start, length)));
            cursor = start + length;
        }, this);
        if (cursor < this.text.length) {
            this.passage.appendChild(document.createTextNode(this.text.slice(cursor)));
        }
    };

    MjyTextHighlight.prototype.renderSpot = function (item, piece) {
        var spot = document.createElement('button');
        spot.type = 'button';
        spot.className = 'mjy-text-highlight__spot';
        spot.setAttribute('data-mjy-segment', item.code);
        spot.textContent = piece;
        spot.addEventListener('click', this.toggle.bind(this, item));
        return spot;
    };

    MjyTextHighlight.prototype.markSpot = function (code) {
        var spot = this.passage.querySelector('[data-mjy-segment="' + code + '"]');
        if (spot) {
            spot.setAttribute('aria-pressed', this.picked[code] ? 'true' : 'false');
        }
    };

    MjyTextHighlight.prototype.toggle = function (item) {
        if (this.picked[item.code]) {
            delete this.picked[item.code];
        } else if (Object.keys(this.picked).length < this.maxRows) {
            this.picked[item.code] = this.tags[0].code;
        }
        this.markSpot(item.code);
        this.renderMarks();
        this.write();
    };

    /** 标记清单按原文顺序排，与写进信封的行序一致。 */
    MjyTextHighlight.prototype.marked = function () {
        return this.segments.filter(function (item) {
            return Object.prototype.hasOwnProperty.call(this.picked, item.code);
        }, this);
    };

    MjyTextHighlight.prototype.renderMarks = function () {
        this.marks.textContent = '';
        this.segments.forEach(function (item) {
            this.markSpot(item.code);
        }, this);
        this.marked().forEach(function (item) {
            var piece = this.text.substr(Number(item.start), Number(item.length));
            var line = document.createElement('li');
            line.className = 'mjy-text-highlight__mark';

            var label = document.createElement('span');
            label.className = 'mjy-text-highlight__piece';
            label.textContent = piece;
            line.appendChild(label);
            line.appendChild(this.renderPicker(item, piece));

            this.marks.appendChild(line);
        }, this);
    };

    MjyTextHighlight.prototype.renderPicker = function (item, piece) {
        var picker = document.createElement('select');
        picker.className = 'form-select mjy-text-highlight__tag';
        picker.setAttribute('aria-label', piece + ' 的标记');
        this.tags.forEach(function (tag) {
            var choice = document.createElement('option');
            choice.value = tag.code;
            choice.textContent = String(tag.label || tag.code);
            choice.selected = tag.code === this.picked[item.code];
            picker.appendChild(choice);
        }, this);
        picker.addEventListener('change', function () {
            this.picked[item.code] = picker.value;
            this.write();
        }.bind(this));
        return picker;
    };

    /** 编辑器的唯一出口：永远整块重写信封，不做增量拼接。 */
    MjyTextHighlight.prototype.write = function () {
        var rows = this.marked().map(function (item) {
            return { segment: item.code, tag: this.picked[item.code] };
        }, this);
        this.textarea.value = JSON.stringify({ v: ENVELOPE_VERSION, rows: rows });
    };

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-text-highlight]'), function (root) {
            new MjyTextHighlight(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
