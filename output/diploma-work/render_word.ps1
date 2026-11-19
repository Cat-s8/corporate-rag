$ErrorActionPreference = 'Stop'
$taskDocPath = 'C:\corporate-rag\output\Диплом Локальная RAG система УФК.docx'
$taskPdfPath = 'C:\corporate-rag\output\diploma-work\diploma-qa.pdf'
$taskPageMap = 'C:\corporate-rag\output\diploma-work\page-map.json'
$wordApp = New-Object -ComObject Word.Application
$wordDoc = $null
try {
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordDoc = $wordApp.Documents.Open($taskDocPath, $false, $false, $false)
    $wordDoc.Repaginate()
    foreach ($taskToc in $wordDoc.TablesOfContents) { $taskToc.Update() }
    $wordDoc.Fields.Update() | Out-Null
    for ($taskPass = 0; $taskPass -lt 3; $taskPass++) {
        foreach ($taskToc in $wordDoc.TablesOfContents) {
            $taskToc.Range.Font.Name = 'Times New Roman'
            $taskToc.Range.Font.Size = 12
            $taskToc.Range.ParagraphFormat.LineSpacingRule = 4
            $taskToc.Range.ParagraphFormat.LineSpacing = 15
            $taskToc.Range.ParagraphFormat.SpaceBefore = 0
            $taskToc.Range.ParagraphFormat.SpaceAfter = 2
            $taskToc.Range.ParagraphFormat.FirstLineIndent = 0
        }
        $wordDoc.Repaginate()
        if ($taskPass -lt 2) {
            foreach ($taskToc in $wordDoc.TablesOfContents) { $taskToc.UpdatePageNumbers() }
        }
    }
    $wordDoc.Save()
    $wordDoc.ExportAsFixedFormat($taskPdfPath, 17)
    $taskHeadings = @()
    foreach ($taskParagraph in $wordDoc.Paragraphs) {
        if ($taskParagraph.OutlineLevel -le 2) {
            $taskHeadings += [PSCustomObject]@{
                text = $taskParagraph.Range.Text.Trim()
                page = $taskParagraph.Range.Information(3)
                level = $taskParagraph.OutlineLevel
            }
        }
    }
    $taskHeadings | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $taskPageMap -Encoding utf8
    [PSCustomObject]@{pages=$wordDoc.ComputeStatistics(2); words=$wordDoc.ComputeStatistics(0); pdf=$taskPdfPath} | ConvertTo-Json
}
finally {
    if ($null -ne $wordDoc) { $wordDoc.Close(0); [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordDoc) | Out-Null }
    try { $wordApp.Quit() } catch { }
    try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp) | Out-Null } catch { }
}
