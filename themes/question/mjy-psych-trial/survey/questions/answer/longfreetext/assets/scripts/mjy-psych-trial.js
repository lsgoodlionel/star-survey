/**
 * MJY 心理实验的实验台（R02-46）。
 *
 * 只负责呈现与计时：逐个试次显示刺激，作答者按一个键，记下用了多少毫秒，
 * 然后进入下一个试次。全部试次走完后整块重写 textarea。
 *
 * 计时用 performance.now()（单调时钟，不受系统时间调整影响），没有它时退回 Date.now()。
 * 这仍然是**浏览器计时**：受渲染节奏、页面隐藏与输入延迟影响，不承诺等同实验室硬件。
 * 本脚本只保证把测到的毫秒数原样、有界地写进信封。
 *
 * 对错在这里一概不判：正确按键由平台按定义推导（mjy_psych_trials.correct），
 * 浏览器端的任何结论都不作数——信封可以被整块替换掉。
 */
(function () {
    'use strict';

    var ENVELOPE_VERSION = 1;
    var KEY_COLUMN = 'key';

    function parseJson(raw, fallback) {
        try {
            var value = JSON.parse(raw || '');
            return Array.isArray(value) ? value : fallback;
        } catch (error) {
            return fallback;
        }
    }

    /** 可按的键就是 key 列的取值集合——列定义已经带着它，不再单独下发一份。 */
    function keyOptions(columns) {
        for (var index = 0; index < columns.length; index += 1) {
            if (columns[index] && columns[index].code === KEY_COLUMN) {
                return Array.isArray(columns[index].options) ? columns[index].options : [];
            }
        }
        return [];
    }

    function now() {
        return (window.performance && typeof window.performance.now === 'function')
            ? window.performance.now()
            : Date.now();
    }

    function MjyPsychTrial(root) {
        this.root = root;
        this.textarea = root.querySelector('.mjy-psych-trial__raw');
        this.editor = root.querySelector('.mjy-psych-trial__editor');
        this.progress = root.querySelector('.mjy-psych-trial__progress');
        this.stimulus = root.querySelector('.mjy-psych-trial__stimulus');
        this.keyBar = root.querySelector('.mjy-psych-trial__keys');
        this.log = root.querySelector('.mjy-psych-trial__log');
        this.trials = parseJson(root.getAttribute('data-mjy-trials'), []);
        this.keys = keyOptions(parseJson(root.getAttribute('data-mjy-columns'), []));
    }

    MjyPsychTrial.prototype.start = function () {
        if (!this.textarea || !this.trials.length || !this.keys.length) {
            return; // 配置缺失时退回纯 textarea，作答者不至于被卡住
        }
        this.rows = [];
        this.index = 0;
        this.textarea.hidden = true;
        this.editor.hidden = false;
        this.renderKeys();
        this.show();
    };

    MjyPsychTrial.prototype.renderKeys = function () {
        this.keyBar.textContent = '';
        this.keys.forEach(function (key) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-outline-secondary mjy-psych-trial__key';
            button.setAttribute('data-mjy-key', key.code);
            button.textContent = String(key.label || key.code);
            button.addEventListener('click', this.answer.bind(this, key.code));
            this.keyBar.appendChild(button);
        }, this);
    };

    MjyPsychTrial.prototype.show = function () {
        var done = this.index >= this.trials.length;
        this.keyBar.hidden = done;
        if (done) {
            this.progress.textContent = '全部 ' + this.trials.length + ' 个试次已完成';
            this.stimulus.textContent = '';
            return;
        }
        var trial = this.trials[this.index];
        this.progress.textContent = '试次 ' + (this.index + 1) + ' / ' + this.trials.length;
        this.stimulus.textContent = String(trial.stimulus || '');
        // 刺激真正出现在屏幕上之后才起表：在上一帧起表会把渲染时间算进反应时。
        var self = this;
        window.requestAnimationFrame(function () {
            self.startedAt = now();
        });
    };

    MjyPsychTrial.prototype.answer = function (code) {
        if (this.index >= this.trials.length || typeof this.startedAt !== 'number') {
            return;
        }
        var elapsed = Math.max(0, Math.round(now() - this.startedAt));
        this.rows.push({ trial: this.trials[this.index].code, key: code, rt: String(elapsed) });
        this.startedAt = null;
        this.index += 1;
        this.appendLog(this.rows[this.rows.length - 1]);
        this.write();
        this.show();
    };

    MjyPsychTrial.prototype.appendLog = function (row) {
        var line = document.createElement('li');
        line.className = 'mjy-psych-trial__entry';
        // 只回显作答者自己刚按的键与毫秒数，不回显对错——对错不是浏览器说了算。
        line.textContent = row.trial + '：' + row.key + '，' + row.rt + ' ms';
        this.log.appendChild(line);
    };

    /** 编辑器的唯一出口：永远整块重写信封，不做增量拼接。 */
    MjyPsychTrial.prototype.write = function () {
        this.textarea.value = JSON.stringify({ v: ENVELOPE_VERSION, rows: this.rows });
    };

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-psych-trial]'), function (root) {
            new MjyPsychTrial(root).start();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
