# Rasterize the code-native DwellMind mark for Unraid's PNG icon convention.
Add-Type -AssemblyName System.Drawing
$logoPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'web/icon.png'
$bitmap=New-Object System.Drawing.Bitmap 256,256
$graphics=[System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::Transparent)
$graphics.ScaleTransform(2,2)
$outline=New-Object System.Drawing.Drawing2D.GraphicsPath
$outline.AddArc(2,2,58,58,180,90)
$outline.AddArc(68,2,58,58,270,90)
$outline.AddArc(68,68,58,58,0,90)
$outline.AddArc(2,68,58,58,90,90)
$outline.CloseFigure()
$background=New-Object System.Drawing.Drawing2D.LinearGradientBrush ([System.Drawing.Point]::new(2,2)),([System.Drawing.Point]::new(126,126)),([System.Drawing.ColorTranslator]::FromHtml('#172940')),([System.Drawing.ColorTranslator]::FromHtml('#111428'))
$graphics.FillPath($background,$outline)
$border=New-Object System.Drawing.Pen ([System.Drawing.ColorTranslator]::FromHtml('#384961')),2
$graphics.DrawPath($border,$outline)
$gradient=New-Object System.Drawing.Drawing2D.LinearGradientBrush ([System.Drawing.Point]::new(27,29)),([System.Drawing.Point]::new(101,93)),([System.Drawing.ColorTranslator]::FromHtml('#61ead6')),([System.Drawing.ColorTranslator]::FromHtml('#b398ff'))
$pen=New-Object System.Drawing.Pen $gradient,7
$pen.StartCap=[System.Drawing.Drawing2D.LineCap]::Round
$pen.EndCap=[System.Drawing.Drawing2D.LineCap]::Round
$pen.LineJoin=[System.Drawing.Drawing2D.LineJoin]::Round
$graphics.DrawLines($pen,[System.Drawing.PointF[]]@([System.Drawing.PointF]::new(27,58),[System.Drawing.PointF]::new(64,29),[System.Drawing.PointF]::new(101,58)))
$graphics.DrawLines($pen,[System.Drawing.PointF[]]@([System.Drawing.PointF]::new(37,54),[System.Drawing.PointF]::new(37,93),[System.Drawing.PointF]::new(91,93),[System.Drawing.PointF]::new(91,54)))
$pen.Width=4
$graphics.DrawLines($pen,[System.Drawing.PointF[]]@([System.Drawing.PointF]::new(49,74),[System.Drawing.PointF]::new(64,61),[System.Drawing.PointF]::new(79,74)))
$graphics.DrawLine($pen,64,45,64,93)
$nodeBrush=New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml('#8de5ea'))
foreach($point in @(@(49,74,6),@(64,61,6),@(79,74,6),@(64,45,4))) { $graphics.FillEllipse($nodeBrush,($point[0]-$point[2]),($point[1]-$point[2]),($point[2]*2),($point[2]*2)) }
$bitmap.Save($logoPath,[System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
$outline.Dispose()
$background.Dispose()
$gradient.Dispose()
$pen.Dispose()
$border.Dispose()
$nodeBrush.Dispose()
