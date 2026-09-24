/**
 * MJY 专业模型 KANO 的编辑器（R02-47）。
 *
 * 只负责编辑体验：一行一个功能点，正向问与反向问各一个下拉，改动后整块重写 textarea。
 * 量表直接取自列定义——与插件校验用的是同一份取值集合，不另下发一份会漂的副本。
 *
 * 功能点是否声明过、量表取值是否合法、行数对不对，在这里都只是提示；
 * 真正的判定在服务端（MjyRepeatingTableValidator 的枚举列、唯一列与行数上下限），
 * 因为浏览器端的任何结论都不可信——信封可以被整块替换掉。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;
    var FEATURE_COLUMN = 'feature';

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

    function MjyModelKano(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-model-kano__raw');
        this.editor = root.querySelector('.mjy-model-kano__editor');
        this.head = root.querySelector('.mjy-model-kano__head');
        this.body = root.querySelector('.mjy-model-kano__body');
        this.features = parseJson(root.getAttribute('data-mjy-features'), []);
        this.columns = parseJson(root.getAttribute('data-mjy-columns'), []).filter(function (column) {
            return column && column.code !== FEATURE_COLUMN;
        });
    }

    MjyModelKano.prototype.start = function () {
        if (!this.textarea || !this.features.length || !this.columns.length) {
            return; // 配置缺失时退回纯 textarea，作答者不至于被卡住
        }
        // 已存的作答按功能点代码归位；认不出的行交给服务端去报错，编辑器不猜。
        this.answers = {};
        var known = {};
        this.features.forEach(function (item) {
            known[item.code] = true;
        });
        parseRows(this.textarea.value).forEach(function (row) {
            if (row && typeof row === 'object' && known[row.feature]) {
                this.answers[row.feature] = row;
            }
        }, this);

        this.textarea.hidden = true;
        this.editor.hidden = false;
        this.renderHead();
        this.renderBody();
        this.write();
    };

    MjyModelKano.prototype.renderHead = function () {
        this.head.textContent = '';
        this.head.appendChild(document.createElement('th'));
        this.columns.forEach(function (column) {
            var cell = document.createElement('th');
            cell.scope = 'col';
            cell.textContent = column.label || column.code;
            this.head.appendChild(cell);
        }, this);
    };

    MjyModelKano.prototype.renderBody = function () {
        this.body.textContent = '';
        this.features.forEach(function (feature) {
            var line = document.createElement('tr');
            var name = document.createElement('th');
            name.scope = 'row';
            name.textContent = String(feature.label || feature.code);
            line.appendChild(name);
            this.columns.forEach(function (column) {
                var cell = document.createElement('td');
                cell.appendChild(this.renderPicker(feature, column));
                line.appendChild(cell);
            }, this);
            this.body.appendChild(line);
        }, this);
    };

    MjyModelKano.prototype.renderPicker = function (feature, column) {
        var picker = document.createElement('select');
        picker.className = 'form-select mjy-model-kano__choice';
        picker.setAttribute('aria-label',
            String(feature.label || feature.code) + ' ' + String(column.label || column.code));
        var stored = this.answers[feature.code] || {};

        var blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '请选择';
        blank.selected = !stored[column.code];
        picker.appendChild(blank);

        (column.options || []).forEach(function (option) {
            var choice = document.createElement('option');
            choice.value = option.code;
            choice.textContent = String(option.label || option.code);
            choice.selected = option.code === stored[column.code];
            picker.appendChild(choice);
        });
        picker.addEventListener('change', function () {
            var row = this.answers[feature.code] || { feature: feature.code };
            row[column.code] = picker.value;
            this.answers[feature.code] = row;
            this.write();
        }.bind(this));
        return picker;
    };

    /**
     * 编辑器的唯一出口：永远整块重写信封，不做增量拼接。
     * 行序＝功能点的声明序，且**每个功能点都出一行**（哪怕还没选）——
     * 行数被平台钉死成功能点个数，少一行是服务端要拒收的。
     */
    MjyModelKano.prototype.write = function () {
        var rows = this.features.map(function (feature) {
            var stored = this.answers[feature.code] || {};
            var row = { feature: feature.code };
            this.columns.forEach(function (column) {
                row[column.code] = stored[column.code] || '';
            });
            return row;
        }, this);
        this.textarea.value = JSON.stringify({ v: ENVELOPE_VERSION, rows: rows });
    };

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-model-kano]'), function (root) {
            new MjyModelKano(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
