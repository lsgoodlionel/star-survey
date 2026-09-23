/**
 * MJY 扫码录入（R02-28）。
 *
 * 用浏览器自带的 BarcodeDetector（Chrome/Edge/Android）扫码，扫到就写进输入框。
 * 任何一步不可用（没有 API、没有摄像头、用户拒绝授权）都退回手工输入：
 * 扫码是录入方式，不是校验手段，服务端的长度与格式规则照跑。
 */
(function () {
    'use strict';

    var FORMATS = {
        qr: ['qr_code'],
        barcode: ['code_128', 'code_39', 'ean_13', 'ean_8', 'upc_a', 'upc_e', 'itf'],
    };

    function supportedFormats(mode) {
        if (mode === 'any') {
            return FORMATS.qr.concat(FORMATS.barcode);
        }
        return FORMATS[mode] || FORMATS.qr;
    }

    function say(box, message) {
        var note = box.querySelector('.mjy-scan-input__note');
        if (note) {
            note.textContent = message;
        }
    }

    function stop(stream, video) {
        video.hidden = true;
        video.srcObject = null;
        if (stream) {
            stream.getTracks().forEach(function (track) {
                track.stop();
            });
        }
    }

    function scan(box) {
        var input = box.querySelector('.mjy-scan-input__value');
        var video = box.querySelector('.mjy-scan-input__preview');
        var detector = new window.BarcodeDetector({
            formats: supportedFormats(box.getAttribute('data-mjy-format') || 'qr'),
        });
        var stream = null;
        var stopped = false;

        navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
            .then(function (mediaStream) {
                stream = mediaStream;
                video.srcObject = stream;
                video.hidden = false;
                return video.play();
            })
            .then(function () {
                say(box, '');
                var tick = function () {
                    if (stopped) {
                        return;
                    }
                    detector.detect(video).then(function (codes) {
                        if (codes.length) {
                            stopped = true;
                            input.value = codes[0].rawValue;
                            // 让 ExpressionManager 的相关性/校验重新跑一遍。
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                            stop(stream, video);
                            return;
                        }
                        window.requestAnimationFrame(tick);
                    }).catch(function () {
                        stopped = true;
                        stop(stream, video);
                        say(box, '扫码失败，请手工输入');
                    });
                };
                window.requestAnimationFrame(tick);
            })
            .catch(function () {
                stop(stream, video);
                say(box, '无法打开摄像头，请手工输入');
            });
    }

    function attach(box) {
        var button = box.querySelector('.mjy-scan-input__start');
        var input = box.querySelector('.mjy-scan-input__value');
        if (!button || !input) {
            return;
        }
        var canScan = 'BarcodeDetector' in window
            && navigator.mediaDevices
            && typeof navigator.mediaDevices.getUserMedia === 'function';
        if (!canScan) {
            // 浏览器扫不了就必须留着手工输入，否则这道题谁也填不了。
            say(box, '当前浏览器不支持扫码，请手工输入');
            return;
        }
        button.hidden = false;
        if (box.getAttribute('data-mjy-manual') === '0') {
            input.readOnly = true;
        }
        button.addEventListener('click', function () {
            input.readOnly = false;
            scan(box);
        });
    }

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mjy-scan]'), attach);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
