/**
 * MJY 循环评价的编辑器（R02-11）。
 *
 * 只负责编辑体验：按评价对象逐行铺开，每个维度一个下拉，改动后写回 textarea。
 * 行数、取值范围、必填在这里只是提示；真正的判定在服务端
 * （plugins/MjyQuestionExtensions/MjyRepeatingTableValidator.php 的枚举列与唯一列），
 * 因为浏览器端的任何结论都不可信——行可以被删掉、下拉可以被改成任意字符串。
 *
 * 行序固定为对象声明的顺序，对象列由编辑器自己填，作答者改不了：
 * 这样「每个对象恰好评一次」在正常路径上天然成立，篡改路径由服务端兜住。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;
    var OBJECT_COLUMN = 'target';

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

    function MjyLoopRating(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-loop-rating__raw');
        this.editor = root.querySelector('.mjy-loop-rating__editor');
        this.head = root.querySelector('.mjy-loop-rating__head');
        this.body = root.querySelector('.mjy-loop-rating__body');
        this.columns = parseJson(root.getAttribute('data-mjy-columns'), []);
        this.objects = parseJson(root.getAttribute('data-mjy-objects'), []);
        this.dimensions = this.columns.filter(function (column) {
            return column.code !== OBJECT_COLUMN;
        });
    }

    /** 已存的作答按对象代码归位；对不上的行丢给服务端去报错，编辑器不猜。 */
    MjyLoopRating.prototype.readStored = function () {
        var byObject = {};
        parseRows(this.textarea.value).forEach(function (row) {
            if (row && typeof row === 'object' && typeof row[OBJECT_COLUMN] === 'string') {
                byObject[row[OBJECT_COLUMN]] = row;
            }
        });
        return byObject;
    };

    MjyLoopRating.prototype.start = function () {
        if (!this.textarea || !this.objects.length || !this.dimensions.length) {
            return; // 配置缺失时退回纯 textarea，作答者不至于被卡住
        }
        var stored = this.readStored();
        this.rows = this.objects.map(function (item) {
            var row = { target: item.code };
            this.dimensions.forEach(function (column) {
                var previous = stored[item.code];
                row[column.code] = previous && typeof previous[column.code] === 'string'
                    ? previous[column.code] : '';
            });
            return row;
        }, this);
        this.textarea.hidden = true;
        this.editor.hidden = false;
        this.renderHead();
        this.renderBody();
        this.write();
    };

    MjyLoopRating.prototype.renderHead = function () {
        this.head.textContent = '';
        this.head.appendChild(this.cell('th', ''));
        this.dimensions.forEach(function (column) {
            this.head.appendChild(this.cell('th', String(column.label || column.code)));
        }, this);
    };

    MjyLoopRating.prototype.cell = function (tag, text) {
        var node = document.createElement(tag);
        node.textContent = text;
        return node;
    };

    MjyLoopRating.prototype.renderBody = function () {
        this.body.textContent = '';
        this.objects.forEach(function (item, index) {
            var line = document.createElement('tr');
            line.className = 'mjy-loop-rating__row';
            line.setAttribute('data-mjy-object', item.code);
            line.appendChild(this.cell('th', String(item.label || item.code)));
            this.dimensions.forEach(function (column) {
                var holder = document.createElement('td');
                holder.appendChild(this.select(column, index));
                line.appendChild(holder);
            }, this);
            this.body.appendChild(line);
        }, this);
    };

    MjyLoopRating.prototype.select = function (column, index) {
        var field = document.createElement('select');
        field.className = 'form-select mjy-loop-rating__cell';
        field.setAttribute('aria-label', String(column.label || column.code));
        field.appendChild(new Option('', ''));
        (column.options || []).forEach(function (option) {
            field.appendChild(new Option(String(option.label || option.code), String(option.code)));
        });
        field.value = this.rows[index][column.code] || '';
        field.addEventListener('change', function () {
            this.rows[index][column.code] = field.value;
            this.write();
        }.bind(this));
        return field;
    };

    /** 编辑器的唯一出口：永远整块重写信封，不做增量拼接。 */
    MjyLoopRating.prototype.write = function () {
        this.textarea.value = JSON.stringify({ v: ENVELOPE_VERSION, rows: this.rows });
    };

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-loop-rating]'), function (root) {
            new MjyLoopRating(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
