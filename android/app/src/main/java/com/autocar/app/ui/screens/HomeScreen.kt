package com.autocar.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.ui.components.ConnectionBar
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.viewmodel.SensorsViewModel
import com.autocar.app.viewmodel.SettingsViewModel
import kotlinx.coroutines.delay

@Composable
fun HomeScreen(
    settingsVm: SettingsViewModel = viewModel(),
    sensorsVm: SensorsViewModel = viewModel(),
) {
    val connected by settingsVm.connected.collectAsState()
    val sensorUpdate by sensorsVm.sensorUpdate.collectAsState()

    LaunchedEffect(Unit) {
        while (true) {
            settingsVm.checkConnection()
            if (connected) sensorsVm.fetchStatus()
            delay(3000)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        ConnectionBar(connected = connected)

        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("AutoCar", style = MaterialTheme.typography.headlineLarge)

            Spacer(Modifier.height(4.dp))

            // Connection status card
            StatusCard(
                title = "API Server",
                connected = connected,
                detail = if (connected) "Online" else "Unreachable",
            )

            // Lidar status
            StatusCard(
                title = "Lidar",
                connected = sensorUpdate.lidar?.connected ?: false,
                detail = sensorUpdate.lidar?.let {
                    if (it.connected) "%.1f Hz".format(it.scan_hz) else "Disconnected"
                } ?: "Unknown",
            )

            // Camera status
            StatusCard(
                title = "Camera",
                connected = sensorUpdate.camera?.connected ?: false,
                detail = sensorUpdate.camera?.let {
                    if (it.connected) "%.0f FPS".format(it.fps) else "Disconnected"
                } ?: "Unknown",
            )

            // Drive status
            StatusCard(
                title = "Drive",
                connected = connected,
                detail = sensorUpdate.drive?.let {
                    if (it.moving) "Moving (${it.mode})" else "Idle"
                } ?: "Unknown",
            )

            Spacer(Modifier.height(8.dp))

            TextButton(onClick = { settingsVm.checkConnection() }) {
                Text("Refresh")
            }
        }
    }
}

@Composable
private fun StatusCard(title: String, connected: Boolean, detail: String) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(text = title, style = MaterialTheme.typography.titleLarge)
                Text(text = detail, style = MaterialTheme.typography.bodyLarge)
            }
            Icon(
                imageVector = if (connected) Icons.Default.CheckCircle else Icons.Default.Error,
                contentDescription = null,
                tint = if (connected) StatusGreen else StatusRed,
                modifier = Modifier.size(32.dp),
            )
        }
    }
}
