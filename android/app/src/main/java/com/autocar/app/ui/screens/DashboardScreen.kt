package com.autocar.app.ui.screens

import android.view.InputDevice
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.data.model.MotorDetail
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.GoldDark
import com.autocar.app.ui.theme.GoldLight
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.ui.theme.StatusYellow
import com.autocar.app.viewmodel.SensorsViewModel
import com.autocar.app.viewmodel.SettingsViewModel
import kotlinx.coroutines.delay

@Composable
fun DashboardScreen(
    settingsVm: SettingsViewModel = viewModel(),
    sensorsVm: SensorsViewModel = viewModel(),
) {
    val connected by settingsVm.connected.collectAsState()
    val sensorUpdate by sensorsVm.sensorUpdate.collectAsState()
    var refreshing by remember { mutableStateOf(false) }

    // Detect Bluetooth/USB gamepad
    var controllerName by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        while (true) {
            controllerName = findGamepad()
            settingsVm.checkConnection()
            if (connected) sensorsVm.fetchStatus()
            delay(3000)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Dashboard", style = MaterialTheme.typography.headlineLarge, color = Gold)

        Spacer(Modifier.height(4.dp))

        StatusCard(
            title = "API Server",
            connected = connected,
            detail = if (connected) "Online" else "Unreachable",
        )

        StatusCard(
            title = "Lidar",
            connected = sensorUpdate.lidar?.connected ?: false,
            detail = sensorUpdate.lidar?.let {
                if (it.connected) "%.1f Hz".format(it.scan_hz) else "Disconnected"
            } ?: "Unknown",
        )

        StatusCard(
            title = "Camera",
            connected = sensorUpdate.camera?.connected ?: false,
            detail = sensorUpdate.camera?.let {
                if (it.connected) "%.0f FPS".format(it.fps) else "Disconnected"
            } ?: "Unknown",
        )

        StatusCard(
            title = "Drive",
            connected = sensorUpdate.motors?.receiver_online ?: false,
            detail = sensorUpdate.motors?.let { m ->
                val n = m.connected.size
                val driveState = sensorUpdate.drive?.let {
                    if (it.moving) "Moving" else "Idle"
                } ?: "Unknown"
                "$driveState | $n/4 motors"
            } ?: (sensorUpdate.drive?.let {
                if (it.moving) "Moving (${it.mode})" else "Idle"
            } ?: "Unknown"),
        )

        // Controller card
        StatusCard(
            title = "Controller",
            connected = controllerName != null,
            detail = controllerName ?: "No controller found",
            icon = Icons.Default.Bluetooth,
        )

        sensorUpdate.motors?.let { motorsData ->
            Text("Motors", style = MaterialTheme.typography.titleMedium, color = Gold)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                for (nid in 0..3) {
                    val motor = motorsData.motors[nid.toString()]
                    MotorCard(
                        nodeId = nid,
                        motor = motor,
                        isConnected = nid in motorsData.connected,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }

        Spacer(Modifier.height(4.dp))

        // Refresh button with feedback
        Button(
            onClick = {
                refreshing = true
                settingsVm.checkConnection()
                sensorsVm.fetchStatus()
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !refreshing,
        ) {
            if (refreshing) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
                Spacer(Modifier.width(8.dp))
                Text("Refreshing...")
            } else {
                Icon(Icons.Default.Refresh, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Refresh")
            }
        }

        // Auto-clear refreshing state
        if (refreshing) {
            LaunchedEffect(Unit) {
                delay(1500)
                refreshing = false
            }
        }

        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

        // ── Settings dropdown ──
        SettingsDropdown(settingsVm = settingsVm, connected = connected)
    }
}

private fun findGamepad(): String? {
    val ids = InputDevice.getDeviceIds()
    for (id in ids) {
        val device = InputDevice.getDevice(id) ?: continue
        val isGamepad = device.sources and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD
        val isJoystick = device.sources and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK
        if (isGamepad || isJoystick) {
            return device.name
        }
    }
    return null
}

// ── Settings collapsible section ──

@Composable
private fun SettingsDropdown(
    settingsVm: SettingsViewModel,
    connected: Boolean,
) {
    var expanded by remember { mutableStateOf(false) }
    val host by settingsVm.host.collectAsState()
    val port by settingsVm.port.collectAsState()
    val token by settingsVm.token.collectAsState()

    var editHost by remember(host) { mutableStateOf(host) }
    var editPort by remember(port) { mutableStateOf(port.toString()) }
    var editToken by remember(token) { mutableStateOf(token) }

    var saving by remember { mutableStateOf(false) }
    var saveResult by remember { mutableStateOf<Boolean?>(null) }

    Column {
        // Header row — tap to toggle
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded }
                .padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    Icons.Default.Settings,
                    contentDescription = null,
                    tint = Gold,
                    modifier = Modifier.size(20.dp),
                )
                Text("Settings", style = MaterialTheme.typography.titleMedium, color = Gold)
            }
            Icon(
                imageVector = if (expanded) Icons.Default.KeyboardArrowUp
                    else Icons.Default.KeyboardArrowDown,
                contentDescription = if (expanded) "Collapse" else "Expand",
                tint = Gold,
            )
        }

        // Expandable body
        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically(),
            exit = shrinkVertically(),
        ) {
            Column(
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.padding(top = 4.dp),
            ) {
                OutlinedTextField(
                    value = editHost,
                    onValueChange = { editHost = it },
                    label = { Text("Host IP") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                )

                OutlinedTextField(
                    value = editPort,
                    onValueChange = { editPort = it },
                    label = { Text("Port") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )

                OutlinedTextField(
                    value = editToken,
                    onValueChange = { editToken = it },
                    label = { Text("API Token (optional)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )

                Button(
                    onClick = {
                        saving = true
                        saveResult = null
                        settingsVm.setHost(editHost.trim())
                        editPort.trim().toIntOrNull()?.let { settingsVm.setPort(it) }
                        settingsVm.setToken(editToken.trim())
                        settingsVm.checkConnection()
                    },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !saving,
                    colors = ButtonDefaults.buttonColors(containerColor = Gold),
                ) {
                    if (saving) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = GoldDark,
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Testing...", color = GoldDark)
                    } else {
                        Text("Save & Test Connection", color = GoldDark)
                    }
                }

                // Show result after save
                if (saving) {
                    LaunchedEffect(Unit) {
                        delay(2000)
                        saveResult = connected
                        saving = false
                    }
                }

                when (saveResult) {
                    true -> Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(Icons.Default.CheckCircle, null, tint = StatusGreen, modifier = Modifier.size(18.dp))
                        Text("Connected", color = StatusGreen, style = MaterialTheme.typography.bodyLarge)
                    }
                    false -> Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(Icons.Default.Error, null, tint = StatusRed, modifier = Modifier.size(18.dp))
                        Text("Connection failed", color = StatusRed, style = MaterialTheme.typography.bodyLarge)
                    }
                    null -> {
                        Text(
                            text = if (connected) "Connected" else "Not connected",
                            color = if (connected) StatusGreen else StatusRed,
                            style = MaterialTheme.typography.bodyLarge,
                        )
                    }
                }
            }
        }
    }
}

// ── Shared components ──

@Composable
private fun StatusCard(
    title: String,
    connected: Boolean,
    detail: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector? = null,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(text = title, style = MaterialTheme.typography.titleLarge, color = GoldLight)
                Text(text = detail, style = MaterialTheme.typography.bodyLarge)
            }
            Icon(
                imageVector = icon
                    ?: if (connected) Icons.Default.CheckCircle else Icons.Default.Error,
                contentDescription = null,
                tint = if (connected) StatusGreen else StatusRed,
                modifier = Modifier.size(32.dp),
            )
        }
    }
}

@Composable
private fun MotorCard(
    nodeId: Int,
    motor: MotorDetail?,
    isConnected: Boolean,
    modifier: Modifier = Modifier,
) {
    val role = motor?.role ?: listOf("BL", "FL", "BR", "FR").getOrElse(nodeId) { "?" }
    val hasError = (motor?.error ?: 0) != 0
    val isArmed = motor?.armed ?: false

    val indicatorColor = when {
        hasError -> StatusRed
        !isConnected -> StatusRed
        isArmed -> StatusGreen
        else -> StatusYellow
    }

    Card(modifier = modifier) {
        Column(
            modifier = Modifier.padding(8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(10.dp)
                        .clip(CircleShape)
                        .background(indicatorColor)
                )
                Text(text = role, style = MaterialTheme.typography.titleSmall)
            }
            if (motor != null && isConnected) {
                Text(
                    text = "%.1fA".format(motor.current),
                    style = MaterialTheme.typography.bodySmall,
                )
                if (hasError) {
                    Text(
                        text = "ERR",
                        style = MaterialTheme.typography.labelSmall,
                        color = StatusRed,
                    )
                }
            } else {
                Text(
                    text = "N/C",
                    style = MaterialTheme.typography.bodySmall,
                    color = StatusRed,
                )
            }
        }
    }
}
