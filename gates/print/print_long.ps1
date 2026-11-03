# Print 600-dpi page images on Long bond paper (8.5 x 13 in) at 100% scale, plain paper, Canon G4070.
# Adapted from the g4070-print skill's print.ps1 (that one is A4 only).
#   powershell -NoProfile -STA -ExecutionPolicy Bypass -File print_long.ps1 -Pages a_1.png,a_2.png [-XpsOut dry.xps]
param(
  [Parameter(Mandatory)][string[]]$Pages,
  [int]$Copies = 1,
  [string]$Printer = 'Canon G4070 series',
  [string]$XpsOut
)
$ErrorActionPreference = 'Stop'
$Pages = $Pages | ForEach-Object { $_ -split ',' }   # -File passes "a.png,b.png" as one string
Add-Type -AssemblyName PresentationCore, PresentationFramework, WindowsBase, ReachFramework, System.Printing

$W = 8.5 * 96; $H = 13 * 96   # page size in WPF units (1/96 in): 1 image pixel at 600 dpi = exact size
$doc = New-Object System.Windows.Documents.FixedDocument
foreach ($pg in $Pages) {
  $bmp = New-Object System.Windows.Media.Imaging.BitmapImage
  $bmp.BeginInit(); $bmp.CacheOption = 'OnLoad'; $bmp.UriSource = [Uri](Resolve-Path $pg).Path; $bmp.EndInit()
  $fp = New-Object System.Windows.Documents.FixedPage; $fp.Width = $W; $fp.Height = $H
  $img = New-Object System.Windows.Controls.Image
  $img.Source = $bmp; $img.Width = $W; $img.Height = $H; $img.Stretch = 'Fill'
  [void]$fp.Children.Add($img)
  $fp.Measure((New-Object System.Windows.Size $W, $H)); $fp.Arrange((New-Object System.Windows.Rect 0, 0, $W, $H))
  $pc = New-Object System.Windows.Documents.PageContent; ([System.Windows.Markup.IAddChild]$pc).AddChild($fp)
  [void]$doc.Pages.Add($pc)
}

$queue = New-Object System.Printing.PrintQueue((New-Object System.Printing.LocalPrintServer), $Printer)
$psf = 'http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework'
$caps = [xml]([Text.Encoding]::UTF8.GetString($queue.GetPrintCapabilitiesAsXml().ToArray()))
$cn = New-Object System.Xml.XmlNamespaceManager $caps.NameTable; $cn.AddNamespace('psf', $psf)
# The driver's own paper option for 8.5 x 13 in (215.9 x 330.2 mm, values in microns).
$size = $null
foreach ($o in $caps.SelectNodes("//psf:Feature[@name='psk:PageMediaSize']/psf:Option", $cn)) {
  $w = $o.SelectSingleNode("psf:ScoredProperty[@name='psk:MediaSizeWidth']/psf:Value", $cn)
  $h = $o.SelectSingleNode("psf:ScoredProperty[@name='psk:MediaSizeHeight']/psf:Value", $cn)
  if ($w -and $h -and [math]::Abs([int]$w.InnerText - 215900) -lt 1000 -and [math]::Abs([int]$h.InnerText - 330200) -lt 1000) {
    $size = $o.GetAttribute('name'); break
  }
}
if (-not $size) { throw 'The driver has no 8.5 x 13 in paper option.' }

$t = $queue.DefaultPrintTicket.Clone()
$t.PageOrientation = 'Portrait'; $t.PageBorderless = 'None'; $t.CopyCount = $Copies; $t.Duplexing = 'OneSided'
$t.OutputColor = 'Grayscale'
$ms = New-Object System.IO.MemoryStream; $t.SaveTo($ms); $ms.Position = 0
$xml = New-Object System.Xml.XmlDocument; $xml.Load($ms)
$nsm = New-Object System.Xml.XmlNamespaceManager $xml.NameTable; $nsm.AddNamespace('psf', $psf)
foreach ($pair in @(@('psk:PageMediaSize', $size), @('psk:PageMediaType', 'psk:Plain'))) {
  $feat = $xml.SelectSingleNode("//psf:Feature[@name='$($pair[0])']", $nsm)
  if (-not $feat) { $feat = $xml.CreateElement('psf', 'Feature', $psf); [void]$xml.DocumentElement.AppendChild($feat) }
  $feat.RemoveAll(); $feat.SetAttribute('name', $pair[0])
  $opt = $xml.CreateElement('psf', 'Option', $psf); $opt.SetAttribute('name', $pair[1]); [void]$feat.AppendChild($opt)
}
$prefix = $size.Split(':')[0]
if (-not $xml.DocumentElement.GetAttribute("xmlns:$prefix")) {
  $xml.DocumentElement.SetAttribute("xmlns:$prefix", $caps.DocumentElement.GetAttribute("xmlns:$prefix"))
}
$ms2 = New-Object System.IO.MemoryStream; $xml.Save($ms2); $ms2.Position = 0
$ticket = $queue.MergeAndValidatePrintTicket($queue.DefaultPrintTicket, (New-Object System.Printing.PrintTicket($ms2))).ValidatedPrintTicket
$ms3 = New-Object System.IO.MemoryStream; $ticket.SaveTo($ms3)
if ([Text.Encoding]::UTF8.GetString($ms3.ToArray()) -notmatch [regex]::Escape($size.Split(':')[1])) { throw "Driver rejected paper $size" }
Write-Host ("Ticket: paper={0} ({1:N1} x {2:N1} in) plain, grayscale, copies={3}, pages={4}" -f $size,
  ($ticket.PageMediaSize.Width / 96), ($ticket.PageMediaSize.Height / 96), $Copies, $doc.Pages.Count)

if ($XpsOut) {
  $xps = New-Object System.Windows.Xps.Packaging.XpsDocument($XpsOut, [System.IO.FileAccess]::ReadWrite)
  [System.Windows.Xps.Packaging.XpsDocument]::CreateXpsDocumentWriter($xps).Write($doc, $ticket); $xps.Close()
  Write-Host "Dry run written to $XpsOut"
  return
}

$p = Get-CimInstance Win32_Printer -Filter "Name='$Printer'"
if ($p.WorkOffline -or $p.PrinterStatus -notin 3, 4) { Write-Warning "Printer status $($p.PrinterStatus) (3 = idle). Check it is on and online." }
$before = @(Get-PrintJob -PrinterName $Printer | ForEach-Object Id)
[System.Printing.PrintQueue]::CreateXpsDocumentWriter($queue).Write($doc, $ticket)
$job = Get-PrintJob -PrinterName $Printer | Where-Object { $_.Id -notin $before } | Sort-Object Id | Select-Object -Last 1
Write-Host "Sent to $Printer as job $($job.Id)"
$t0 = Get-Date; $last = ''
while ($job -and ((Get-Date) - $t0).TotalSeconds -lt 600) {
  Start-Sleep 3
  $j = Get-PrintJob -PrinterName $Printer | Where-Object Id -eq $job.Id
  $s = if ($j) { "$($j.JobStatus), pages printed $($j.PagesPrinted)" } else { 'done (left the queue)' }
  if ($s -ne $last) { Write-Host ("{0,4:N0}s  {1}" -f ((Get-Date) - $t0).TotalSeconds, $s); $last = $s }
  if (-not $j) { break }
  if ($j.JobStatus -match 'Error|Offline|Paper|Blocked|UserIntervention') {
    Write-Warning 'Job stalled: check the printer screen (paper size/type prompt? jam?).'
    break
  }
}
$p = Get-CimInstance Win32_Printer -Filter "Name='$Printer'"
Write-Host "Printer status $($p.PrinterStatus) (3 = idle), error state $($p.DetectedErrorState) (0/2 = none)"
