/**
 * MJY 多级下拉（R02-03）的作答页编辑器。
 *
 * 与别的 mjy- 主题不同：可选项**不在页面里**。行政区划有数千个节点，一次全下发既慢又没必要，
 * 所以每一级都在需要时向插件的 dictionaryNodes 端点取一页（ADR 0019 决定 4）。
 *
 * 浏览器端的任何结论都不作数：真正的把关是服务端逐级核对整条路径
 * （MjyRepeatingTableValidator::checkDictionaryPaths）。这里只负责让人选得动。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;
    var PAGE_SIZE = 200;
    var ENDPOINT = 'index.php/plugins/direct';
    var PLUGIN = 'MjyQuestionExtensions';
    var FUNCTION = 'dictionaryNodes';

    function parseJson(raw, fallback) {
        if (typeof raw !== 'string' || raw === '') {
            return fallback;
        }
        try {
            var value = JSON.parse(raw);
            return value === null ? fallback : value;
        } catch (error) {
            return fallback;
        }
    }

    function firstRow(raw) {
        var envelope = parseJson(raw, null);
        if (!envelope || envelope.v !== ENVELOPE_VERSION || !Array.isArray(envelope.rows)) {
            return {};
        }
        return envelope.rows.length > 0 && envelope.rows[0] ? envelope.rows[0] : {};
    }

    function MjyCascadingSelect(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-cascading-select__raw');
        this.editor = root.querySelector('.mjy-cascading-select__editor');
        this.container = root.querySelector('.mjy-cascading-select__levels');
        this.status = root.querySelector('.mjy-cascading-select__status');
        this.sid = root.getAttribute('data-mjy-sid');
        this.dictionary = root.getAttribute('data-mjy-dictionary');
        this.version = root.getAttribute('data-mjy-version');
        this.labels = parseJson(root.getAttribute('data-mjy-levels'), []);
        // 列定义里的 code 才是信封的键（L1/L2/…）；标题只用来显示。
        this.columns = parseJson(root.getAttribute('data-mjy-columns'), []).filter(function (column) {
            return column && column.type === 'dict';
        }).sort(function (left, right) {
            return (left.level || 0) - (right.level || 0);
        });
        this.selects = [];
    }

    MjyCascadingSelect.prototype.start = function () {
        // 配置不全时什么都不做，textarea 留在原地——「编辑器坏了」不该等于「这题交不了」。
        if (!this.textarea || !this.container || !this.sid || !this.dictionary
            || !this.version || this.columns.length === 0) {
            return;
        }
        this.textarea.hidden = true;
        this.editor.hidden = false;

        var stored = firstRow(this.textarea.value);
        var self = this;
        this.columns.forEach(function (column, index) {
            self.selects.push(self.buildLevel(column, index));
        });
        this.loadLevel(0, null, stored);
    };

    MjyCascadingSelect.prototype.buildLevel = function (column, index) {
        var wrapper = document.createElement('div');
        wrapper.className = 'mjy-cascading-select__level';

        var id = this.textarea.id + '-level-' + (index + 1);
        var label = document.createElement('label');
        label.className = 'mjy-cascading-select__label';
        label.setAttribute('for', id);
        label.textContent = this.labels[index] || column.label || column.code;

        var select = document.createElement('select');
        select.className = 'form-select mjy-cascading-select__select';
        select.id = id;
        select.disabled = true;
        select.setAttribute('data-mjy-level', String(index));

        var self = this;
        select.addEventListener('change', function () {
            self.onPick(index);
        });

        wrapper.appendChild(label);
        wrapper.appendChild(select);
        this.container.appendChild(wrapper);
        return select;
    };

    /** 选了第 index 级：后面各级一律清空重取——上一级变了，下面选过的就不再成立。 */
    MjyCascadingSelect.prototype.onPick = function (index) {
        for (var next = index + 1; next < this.selects.length; next++) {
            this.clearLevel(next);
        }
        this.write();
        var picked = this.selects[index].value;
        if (picked !== '' && index + 1 < this.selects.length) {
            this.loadLevel(index + 1, picked, {});
        }
    };

    MjyCascadingSelect.prototype.clearLevel = function (index) {
        var select = this.selects[index];
        select.innerHTML = '';
        select.disabled = true;
    };

    /**
     * 取第 index 级的选项。``stored`` 里有这一级的值时选上它并继续往下取，
     * 这样断点续答回来能把整条路径摆回去。
     */
    MjyCascadingSelect.prototype.loadLevel = function (index, parent, stored) {
        var self = this;
        var select = this.selects[index];
        this.say('正在载入' + (this.labels[index] || '') + '…');
        this.fetchNodes(parent, function (nodes) {
            select.innerHTML = '';
            select.appendChild(self.placeholder());
            nodes.forEach(function (node) {
                var option = document.createElement('option');
                option.value = node.code;
                option.textContent = node.label;
                select.appendChild(option);
            });
            select.disabled = nodes.length === 0;
            self.say('');

            var wanted = stored ? stored[self.columns[index].code] : '';
            if (wanted && select.querySelector('option[value="' + CSS.escape(wanted) + '"]')) {
                select.value = wanted;
                self.write();
                if (index + 1 < self.selects.length) {
                    self.loadLevel(index + 1, wanted, stored);
                }
            }
        });
    };

    MjyCascadingSelect.prototype.placeholder = function () {
        var option = document.createElement('option');
        option.value = '';
        option.textContent = '请选择';
        return option;
    };

    MjyCascadingSelect.prototype.fetchNodes = function (parent, done) {
        var self = this;
        var query = [
            'plugin=' + encodeURIComponent(PLUGIN),
            'function=' + encodeURIComponent(FUNCTION),
            'sid=' + encodeURIComponent(this.sid),
            'dictionary=' + encodeURIComponent(this.dictionary),
            'version=' + encodeURIComponent(this.version),
            'parent=' + encodeURIComponent(parent === null ? '' : parent),
            'limit=' + PAGE_SIZE
        ].join('&');

        var request = new XMLHttpRequest();
        request.open('GET', ENDPOINT + '?' + query, true);
        request.onreadystatechange = function () {
            if (request.readyState !== 4) {
                return;
            }
            if (request.status !== 200) {
                self.say('选项载入失败，请稍后重试');
                done([]);
                return;
            }
            var payload = parseJson(request.responseText, null);
            done(payload && Array.isArray(payload.nodes) ? payload.nodes : []);
        };
        request.send();
    };

    /** 整题一行：一行就是整条路径，没选到的级留空（服务端会因为必答拒掉）。 */
    MjyCascadingSelect.prototype.write = function () {
        var row = {};
        var self = this;
        this.columns.forEach(function (column, index) {
            row[column.code] = self.selects[index].value || '';
        });
        this.textarea.value = JSON.stringify({ v: ENVELOPE_VERSION, rows: [row] });
    };

    MjyCascadingSelect.prototype.say = function (message) {
        if (this.status) {
            this.status.textContent = message;
        }
    };

    function boot() {
        var roots = document.querySelectorAll('[data-mjy-cascading-select]');
        Array.prototype.forEach.call(roots, function (root) {
            new MjyCascadingSelect(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
}());
