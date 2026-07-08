package com.autocar.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.viewmodel.SensorsViewModel

@Composable
fun SensorViewport(
    sensorsVm: SensorsViewModel = viewModel(),
    modifier: Modifier = Modifier,
) {
    val sensorUpdate by sensorsVm.sensorUpdate.collectAsState()
    val cameraUrl by sensorsVm.cameraUrl.collectAsState()
    val depthUrl by sensorsVm.depthUrl.collectAsState()

    LaunchedEffect(Unit) {
        sensorsVm.connect()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 8.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // Camera RGB
        SectionHeader(
            title = "Camera",
            connected = sensorUpdate.camera?.connected ?: false,
            detail = sensorUpdate.camera?.let { if (it.connected) "%.0f FPS".format(it.fps) else null },
        )
        MjpegView(url = cameraUrl)

        // Depth
        SectionHeader(
            title = "Depth",
            connected = sensorUpdate.camera?.connected ?: false,
        )
        MjpegView(url = depthUrl)

        // Lidar
        val lidar = sensorUpdate.lidar
        SectionHeader(
            title = "Lidar",
            connected = lidar?.connected ?: false,
            detail = lidar?.let {
                if (it.connected) "%.1f Hz · ${it.points.size} pts".format(it.scan_hz) else null
            },
        )
        LidarPolarPlot(
            points = lidar?.points ?: emptyList(),
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun SectionHeader(
    title: String,
    connected: Boolean,
    detail: String? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge, color = Gold)
            Text(
                text = if (connected) "LIVE" else "OFF",
                style = MaterialTheme.typography.labelMedium,
                color = if (connected) StatusGreen else StatusRed,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        if (detail != null) {
            Text(
                text = detail,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}
