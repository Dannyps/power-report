<?php
// power-report: web/power-report.php, installed as /var/www/html/internal/power-report.php (serve it behind a login).
// On-demand power report: drops a trigger file that power-report-request.path (root) watches.
// JSON POST only, so a cross-site form cannot fire it (same guard as tap/api.php?action=fav).
header('Content-Type: application/json');
header('Cache-Control: no-store');
if ($_SERVER['REQUEST_METHOD'] !== 'POST' || stripos($_SERVER['CONTENT_TYPE'] ?? '', 'application/json') !== 0) {
    http_response_code(405);
    echo json_encode(['ok' => false, 'error' => 'POST application/json only']);
    exit;
}
if (!@touch('/run/power-report/request')) {
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'could not write the request file']);
    exit;
}
echo json_encode(['ok' => true]);
