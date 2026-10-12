param([Parameter(Mandatory = $true)][string]$DocumentPath)

# Open only the adapter's temporary copy. Macro execution and link updates are
# disabled; Word is a read provider, never a conversion/write owner here.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$word = $null
$document = $null
$ownedProcess = $null
$existingProcesses = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
try {
    $word = New-Object -ComObject Word.Application
    $newProcesses = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object { $existingProcesses -notcontains $_.Id })
    if ($newProcesses.Count -ne 1) {
        throw 'Word could not supply an isolated new background process'
    }
    $ownedProcess = $newProcesses[0]
    # A timeout can kill this PowerShell host before finally runs. Give its
    # Python owner a PID plus creation-time identity to retire only this worker.
    [Console]::Out.WriteLine((@{
        owned_process_pid = $ownedProcess.Id
        owned_process_start_ticks = $ownedProcess.StartTime.ToUniversalTime().Ticks
    } | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $wordOptions = $word.Options
    $previousLinks = $wordOptions.UpdateLinksAtOpen
    $previousPrompt = $wordOptions.SaveNormalPrompt
    $wordOptions.UpdateLinksAtOpen = $false
    $wordOptions.SaveNormalPrompt = $false
    $missing = [Type]::Missing
    $sourcePath = [System.IO.Path]::GetFullPath($DocumentPath)
    $no = $false
    $yes = $true
    $noPassword = '__perspirator_no_password__'
    $empty = ''
    $autoFormat = 0
    $encoding = 65001
    $direction = 0
    $document = $word.Documents.Open(
        [ref]$sourcePath, [ref]$no, [ref]$yes, [ref]$no,
        [ref]$noPassword, [ref]$empty, [ref]$yes,
        [ref]$noPassword, [ref]$empty, [ref]$autoFormat, [ref]$encoding,
        [ref]$no, [ref]$no, [ref]$direction, [ref]$yes, [ref]$empty)
    $sections = @()
    $body = [string]$document.Content.Text
    if ($body.Length -gt 16777216) { throw 'Word text exceeds character bound' }
    $sections += [ordered]@{ title = 'Document body'; text = $body }
    foreach ($kind in @('Footnotes', 'Endnotes')) {
        $notes = $document.$kind
        if ($notes.Count -gt 5000) { throw 'Word notes exceed section bound' }
        $texts = @()
        for ($index = 1; $index -le $notes.Count; $index++) {
            $texts += [string]$notes.Item($index).Range.Text
        }
        if ($texts.Count) {
            $sections += [ordered]@{ title = $kind; text = ($texts -join "`n`n") }
        }
    }
    [ordered]@{ engine = 'Microsoft Word COM'; sections = $sections } | ConvertTo-Json -Depth 5 -Compress
} catch {
    [Console]::Error.WriteLine($_.ScriptStackTrace)
    throw
} finally {
    if ($null -ne $document) {
        $saveChanges = 0
        $document.Close([ref]$saveChanges)
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
    }
    if ($null -ne $wordOptions) {
        $wordOptions.UpdateLinksAtOpen = $previousLinks
        $wordOptions.SaveNormalPrompt = $previousPrompt
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordOptions)
    }
    if ($null -ne $word -and $null -ne $ownedProcess) {
        $saveChanges = 0
        $word.Quit([ref]$saveChanges)
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
        if (-not $ownedProcess.WaitForExit(3000)) {
            # Only the new hidden process whose identity was captured above.
            $ownedProcess.Kill()
            $ownedProcess.WaitForExit(3000) | Out-Null
        }
        $ownedProcess.Dispose()
    } elseif ($null -ne $word) {
        # Never quit an existing user-owned instance.
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
    }
}
