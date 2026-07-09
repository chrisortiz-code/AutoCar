package com.autocar.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.sp
import com.autocar.app.data.model.LidarPoint
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

private val BgColor = Color(0xFF111111)
private val RingColor = Color(0xFF2A2A2A)
private val AxisColor = Color(0xFF333333)
private val CenterColor = Color(0xFF888888)
private val LabelColor = Color(0xFF666666)

private fun niceStep(maxM: Float): Float = when {
    maxM <= 1f -> 0.25f
    maxM <= 3f -> 0.5f
    maxM <= 8f -> 1f
    maxM <= 20f -> 2f
    else -> 5f
}

private fun distColor(m: Float, maxM: Float): Color {
    val t = (m / maxM).coerceIn(0f, 1f)
    val r = (255 * (1f - t)).toInt()
    val g = (180 * t).toInt()
    val b = (60 * t).toInt()
    return Color(r, g, b)
}

@Composable
fun LidarPolarPlot(
    points: List<LidarPoint>,
    modifier: Modifier = Modifier,
) {
    val textMeasurer = rememberTextMeasurer()
    // Persist rangeM across recompositions for smooth scaling
    var rangeM by remember { mutableStateOf(1f) }

    Canvas(
        modifier = modifier
            .fillMaxWidth()
            .aspectRatio(1f),
    ) {
        val w = size.width
        val h = size.height
        val cx = w / 2f
        val cy = h / 2f
        val rMax = min(w, h) * 0.46f

        // Find max distance for auto-scaling
        var maxDist = 0f
        for (p in points) {
            if (p.dist_mm > maxDist) maxDist = p.dist_mm
        }
        val dataMaxM = maxDist / 1000f
        val targetM = max(0.5f, dataMaxM * 1.1f)
        // Smooth transition like the HTML viewer
        rangeM = rangeM + (targetM - rangeM) * 0.3f
        if (rangeM < 0.5f) rangeM = 0.5f

        // Background
        drawRect(BgColor, Offset.Zero, Size(w, h))

        // Concentric range rings
        drawGrid(cx, cy, rMax, rangeM, textMeasurer)

        // Draw points
        for (p in points) {
            val m = p.dist_mm / 1000f
            if (m <= 0f) continue
            val rad = ((p.angle - 90f) * PI / 180f).toFloat()
            val r = (m / rangeM) * rMax
            val x = cx + r * cos(rad)
            val y = cy + r * sin(rad)
            drawRect(
                color = distColor(m, rangeM),
                topLeft = Offset(x - 1.5f, y - 1.5f),
                size = Size(3f, 3f),
            )
        }
    }
}

private fun DrawScope.drawGrid(
    cx: Float,
    cy: Float,
    rMax: Float,
    rangeM: Float,
    textMeasurer: TextMeasurer,
) {
    // Range rings
    val step = niceStep(rangeM)
    var m = step
    while (m <= rangeM) {
        val r = (m / rangeM) * rMax
        drawCircle(
            color = RingColor,
            radius = r,
            center = Offset(cx, cy),
            style = Stroke(width = 1f),
        )
        m += step
    }

    // Cross-hair axes
    drawLine(AxisColor, Offset(cx, cy - rMax), Offset(cx, cy + rMax), strokeWidth = 1f)
    drawLine(AxisColor, Offset(cx - rMax, cy), Offset(cx + rMax, cy), strokeWidth = 1f)

    // Range labels
    val labelStyle = TextStyle(color = LabelColor, fontSize = 10.sp)
    val zeroLabel = textMeasurer.measure("0 m", labelStyle)
    drawText(zeroLabel, topLeft = Offset(cx + 6f, cy - zeroLabel.size.height - 2f))

    val maxLabel = textMeasurer.measure("%.1f m".format(rangeM), labelStyle)
    drawText(maxLabel, topLeft = Offset(cx + 6f, cy - rMax + 4f))

    // Center dot
    drawCircle(CenterColor, radius = 4f, center = Offset(cx, cy))
}
