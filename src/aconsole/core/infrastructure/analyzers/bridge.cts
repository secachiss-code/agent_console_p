const fs: typeof import("node:fs/promises") = require("node:fs/promises");
const path: typeof import("node:path") = require("node:path");
const vscode: typeof import("vscode") = require("vscode");

type Job = {
    extension: string;
    sources: string[];
    files: string[];
    output: string;
};

async function analyze(job: Job) {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
        throw new Error("The managed analysis workspace was not opened");
    }
    const workspace = folder.uri.fsPath;
    const files = new Map(job.files.map(file => [
        vscode.Uri.file(path.join(workspace, file)).toString(), file,
    ]));
    const observed = new Set<string>();
    const subscription = vscode.languages.onDidChangeDiagnostics(event => {
        for (const uri of event.uris) {
            if (files.has(uri.toString()) && hasAnalyzerDiagnostics(uri, job.sources)) {
                observed.add(uri.toString());
            }
        }
    });
    try {
        const extension = vscode.extensions.getExtension(job.extension);
        if (!extension) {
            throw new Error("Analyzer extension is missing from the managed profile");
        }
        await extension.activate();
        for (const uri of files.keys()) {
            const document = await vscode.workspace.openTextDocument(vscode.Uri.parse(uri));
            await vscode.window.showTextDocument(document, { preview: false });
        }
        await new Promise(resolve => setTimeout(resolve, 7000));
        const diagnostics = [];
        for (const [uri, file] of files) {
            for (const item of vscode.languages.getDiagnostics(vscode.Uri.parse(uri))) {
                if (!job.sources.includes((item.source || "").toLowerCase())) {
                    continue;
                }
                observed.add(uri);
                if (diagnostics.length < 100) {
                    diagnostics.push({
                        path: file,
                        line: item.range.start.line + 1,
                        column: item.range.start.character + 1,
                        end_line: item.range.end.line + 1,
                        end_column: item.range.end.character + 1,
                        severity: vscode.DiagnosticSeverity[item.severity].toLowerCase(),
                        code: item.code === undefined ? null : String(typeof item.code === "object" ? item.code.value : item.code),
                        message: item.message.slice(0, 1000),
                    });
                }
            }
        }
        // The public diagnostics API cannot attribute an empty collection to a source.
        return { diagnostics, observed: [...observed].map(uri => files.get(uri)),
            truncated: diagnostics.length === 100 };
    } finally {
        subscription.dispose();
    }
}

function hasAnalyzerDiagnostics(uri: import("vscode").Uri, sources: string[]) {
    return vscode.languages.getDiagnostics(uri).some(item => sources.includes((item.source || "").toLowerCase()));
}

async function activate() {
    const jobFile = process.env.ACONSOLE_ANALYZER_JOB;
    if (!jobFile) {
        return;
    }
    const job: Job = JSON.parse(await fs.readFile(jobFile, "utf8"));
    let result;
    try {
        result = await analyze(job);
    } catch (error) {
        result = { error: String(error).slice(0, 1000) };
    }
    await fs.writeFile(job.output + ".tmp", JSON.stringify(result));
    await fs.rename(job.output + ".tmp", job.output);
}

module.exports = { activate };
