<?php
// Runs the real MjyEventRelay + MjyHttpEventTransport against a stub Yii DB connection.
class CDbException extends Exception {}

class StubCommand
{
    public static $batches = [];
    public static $updates = [];
    public function select($c) { return $this; }
    public function from($t) { return $this; }
    public function where($w) { return $this; }
    public function order($o) { return $this; }
    public function limit($l) { return $this; }
    public function queryAll() { return array_shift(self::$batches) ?? []; }
    public function update($table, $columns, $condition) { self::$updates[] = [$table, $columns, $condition]; return 1; }
}

class CDbConnection
{
    public $tablePrefix = 'lime_';
    public function createCommand() { return new StubCommand(); }
}

require '/plugins/MjyEventTransport.php';
require '/plugins/MjyHttpEventTransport.php';
require '/plugins/MjyEventLog.php';
require '/plugins/MjyEventRelay.php';

// PDO returns every column as a string, as SELECT * on the engine event log would.
StubCommand::$batches[] = [
    ['id' => '41', 'event_id' => '6f9a1c2e-3b4d-4e5f-8a6b-7c8d9e0f1a2b', 'event_type' => 'response.saved',
     'engine_instance_id' => '华东/engine-01', 'survey_id' => '880001', 'generation' => 'gen-e2e',
     'response_id' => '101', 'source' => 'hook', 'dedupe_key' => null,
     'occurred_at' => '2026-09-21 07:15:40', 'delivered_at' => null],
    ['id' => '42', 'event_id' => '0b1c2d3e-4f50-4a61-9b72-8c93d4e5f607', 'event_type' => 'response.completed',
     'engine_instance_id' => '华东/engine-01', 'survey_id' => '880001', 'generation' => 'gen-e2e',
     'response_id' => '101', 'source' => 'scanner',
     'dedupe_key' => 'response.completed:880001:gen-e2e:101',
     'occurred_at' => '2026-09-21 07:15:42', 'delivered_at' => null],
];

$db = new CDbConnection();
$transport = new MjyHttpEventTransport(getenv('ENDPOINT'), getenv('SECRET'));
$relay = new MjyEventRelay($db, new MjyEventLog($db, 'ignored'), $transport);
$sent = $relay->relay();
echo "relayed=$sent marked=" . json_encode(StubCommand::$updates[0][2] ?? null) . PHP_EOL;
