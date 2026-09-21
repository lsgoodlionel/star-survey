<?php
// Router for `php -S`: dumps the raw request exactly as received.
$headers = [];
foreach (getallheaders() as $name => $value) {
    $headers[] = "$name: $value";
}
file_put_contents('/out/headers.txt', $_SERVER['REQUEST_METHOD'] . ' ' . $_SERVER['REQUEST_URI'] . "\n" . implode("\n", $headers) . "\n");
file_put_contents('/out/body.bin', file_get_contents('php://input'));
header('Content-Type: application/json');
echo '{"received":2,"accepted":2,"duplicates":0}';
