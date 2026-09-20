/**
 * MJY 自增表格的编辑器。
 *
 * 只负责编辑体验：把 textarea 里的 JSON 展开成表格，改动后再写回 textarea。
 * 行数上下限、列类型、必填在这里只是提示；真正的判定在服务端
 * （plugins/MjyQuestionExtensions/MjyRepeatingTableValidator.php），
 * 因为浏览器端的任何结论都不可信。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;

    function parseColumns(raw) {
        try {
            var columns = JSON.parse(raw || '[]');
            return Array.isArray(columns) ? columns : [];
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

    function MjyRepeatingTable(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-repeating-table__raw');
        this.editor = root.querySelector('.mjy-repeating-table__editor');
        this.head = root.querySelector('.mjy-repeating-table__head');
        this.body = root.querySelector('.mjy-repeating-table__body');
        this.addButton = root.querySelector('.mjy-repeating-table__add');
        this.columns = parseColumns(root.getAttribute('data-mjy-columns'));
        this.maxRows = parseInt(root.getAttribute('data-mjy-max-rows'), 10) || 20;
        this.minRows = parseInt(root.getAttribute('data-mjy-min-rows'), 10) || 0;
    }

    MjyRepeatingTable.prototype.start = function () {
        if (!this.textarea || this.columns.length === 0) {
            return; // 列定义缺失时退回纯 textarea，作答者不至于被卡住
        }
        this.rows = parseRows(this.textarea.value);
        while (this.rows.length < this.minRows) {
            this.rows.push({});
        }
        this.textarea.hidden = true;
        this.editor.hidden = false;
        this.renderHead();
        this.renderBody();
        this.addButton.addEventListener('click', this.addRow.bind(this));
    };

    MjyRepeatingTable.prototype.renderHead = function () {
        this.head.textContent = '';
        this.columns.forEach(function (column) {
            var cell = document.createElement('th');
            cell.textContent = column.label || column.code;
            this.head.appendChild(cell);
        }, this);
        this.head.appendChild(document.createElement('th'));
    };

    MjyRepeatingTable.prototype.renderBody = function () {
        this.body.textContent = '';
        this.rows.forEach(function (row, rowIndex) {
            this.body.appendChild(this.buildRow(row, rowIndex));
        }, this);
        this.addButton.disabled = this.rows.length >= this.maxRows;
        this.sync();
    };

    MjyRepeatingTable.prototype.buildRow = function (row, rowIndex) {
        var tr = document.createElement('tr');
        this.columns.forEach(function (column) {
            var td = document.createElement('td');
            var input = document.createElement('input');
            input.type = column.type === 'integer' || column.type === 'decimal' ? 'number' : 'text';
            input.className = 'form-control';
            input.value = row[column.code] === undefined ? '' : row[column.code];
            input.setAttribute('aria-label', column.label || column.code);
            input.addEventListener('input', function () {
                row[column.code] = input.value;
                this.sync();
            }.bind(this));
            td.appendChild(input);
            tr.appendChild(td);
        }, this);

        var actions = document.createElement('td');
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-outline-danger mjy-repeating-table__remove';
        remove.textContent = '删除';
        remove.addEventListener('click', function () {
            this.rows.splice(rowIndex, 1);
            this.renderBody();
        }.bind(this));
        actions.appendChild(remove);
        tr.appendChild(actions);
        return tr;
    };

    MjyRepeatingTable.prototype.addRow = function () {
        if (this.rows.length >= this.maxRows) {
            return;
        }
        this.rows.push({});
        this.renderBody();
    };

    MjyRepeatingTable.prototype.sync = function () {
        var columns = this.columns;
        this.textarea.value = JSON.stringify({
            v: ENVELOPE_VERSION,
            rows: this.rows.map(function (row) {
                var normalised = {};
                columns.forEach(function (column) {
                    normalised[column.code] = row[column.code] === undefined ? '' : String(row[column.code]);
                });
                return normalised;
            })
        });
    };

    function bootstrap() {
        var roots = document.querySelectorAll('[data-mjy-repeating-table]');
        Array.prototype.forEach.call(roots, function (root) {
            new MjyRepeatingTable(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrap);
    } else {
        bootstrap();
    }
})();
