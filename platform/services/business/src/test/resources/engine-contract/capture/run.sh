#!/bin/sh
# Inside the container: start the capture server, then run the real relay against it.
php -S 127.0.0.1:8099 /h/capture.php >/dev/null 2>&1 &
sleep 1
php /h/driver.php
