const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');

function activate(context) {
  const resultPath = process.env.ACONSOLE_RUFF_PROBE_RESULT;
  const sourcePath = process.env.ACONSOLE_RUFF_PROBE_SOURCE;
  let finished = false;
  let poll = () => {};
  const startedAt = new Date().toISOString();
  fs.mkdirSync(path.dirname(resultPath), { recursive: true });
  fs.writeFileSync(resultPath, JSON.stringify({
    probe_activated: true,
    started_at: startedAt,
    source: sourcePath,
    diagnostics: [],
  }, null, 2) + '\n');

  function finish(diagnostics, timedOut) {
    if (finished) return;
    const hasRuff = diagnostics.some((item) => String(
      item.code && typeof item.code === 'object' ? item.code.value : item.code,
    ) === 'F401');
    if (!hasRuff && !timedOut) return;
    finished = true;
    fs.mkdirSync(path.dirname(resultPath), { recursive: true });
    fs.writeFileSync(resultPath, JSON.stringify({
      probe_activated: true,
      started_at: startedAt,
      completed_at: new Date().toISOString(),
      source: sourcePath,
      timed_out: timedOut,
      diagnostics,
    }, null, 2) + '\n');
    setTimeout(() => vscode.commands.executeCommand('workbench.action.closeWindow'), 250);
  }

  context.subscriptions.push(vscode.languages.onDidChangeDiagnostics(() => poll()));
  vscode.workspace.openTextDocument(vscode.Uri.file(sourcePath))
    .then((document) => vscode.window.showTextDocument(document))
    .then((editor) => {
      const deadline = Date.now() + 60000;
      poll = function pollDiagnostics() {
        if (finished || !editor || !fs.existsSync(sourcePath)) return;
        const diagnostics = vscode.languages.getDiagnostics(editor.document.uri).map((item) => ({
          source: item.source,
          code: item.code,
          message: item.message,
          severity: vscode.DiagnosticSeverity[item.severity],
          line: item.range.start.line + 1,
          column: item.range.start.character + 1,
        }));
        finish(diagnostics, false);
        if (!finished && Date.now() >= deadline) finish(diagnostics, true);
      };
      poll();
      const timer = setInterval(() => {
        poll();
        if (finished) clearInterval(timer);
      }, 300);
      context.subscriptions.push({ dispose: () => clearInterval(timer) });
    }, (error) => {
      fs.writeFileSync(resultPath, JSON.stringify({
        probe_activated: true,
        error: String(error),
      }, null, 2) + '\n');
      setTimeout(() => vscode.commands.executeCommand('workbench.action.closeWindow'), 250);
    });
}

function deactivate() {}

module.exports = { activate, deactivate };
