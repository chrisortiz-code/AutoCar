package com.autocar.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.GoldDark
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.ui.theme.StatusYellow
import com.autocar.app.viewmodel.ProcessId
import com.autocar.app.viewmodel.ProcessState
import com.autocar.app.viewmodel.ProcessStatus
import com.autocar.app.viewmodel.TerminalViewModel

@Composable
fun TerminalViewport(
    terminalVm: TerminalViewModel,
    modifier: Modifier = Modifier,
) {
    val state by terminalVm.state.collectAsState()

    Column(modifier = modifier.fillMaxSize().padding(8.dp)) {
        // SSH connection bar
        SshConnectionBar(
            connected = state.sshConnected,
            connecting = state.connecting,
            error = state.connectionError,
            onConnect = { terminalVm.connectSsh() },
            onDisconnect = { terminalVm.disconnectSsh() },
        )

        // Options
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 4.dp),
        ) {
            Checkbox(
                checked = state.diffDrive,
                onCheckedChange = { terminalVm.setDiffDrive(it) },
                colors = CheckboxDefaults.colors(checkedColor = Gold),
            )
            Text("Differential drive", style = MaterialTheme.typography.bodyMedium)
        }

        // Two process panels stacked vertically
        ProcessPanel(
            title = "API Server",
            status = state.api,
            sshConnected = state.sshConnected,
            onStart = { terminalVm.startProcess(ProcessId.API_SERVER) },
            onStop = { terminalVm.stopProcess(ProcessId.API_SERVER) },
            onClear = { terminalVm.clearLog(ProcessId.API_SERVER) },
            modifier = Modifier.weight(1f),
        )

        Spacer(Modifier.size(8.dp))

        ProcessPanel(
            title = "Universal Receiver",
            status = state.receiver,
            sshConnected = state.sshConnected,
            onStart = { terminalVm.startProcess(ProcessId.UNIVERSAL_RECEIVER) },
            onStop = { terminalVm.stopProcess(ProcessId.UNIVERSAL_RECEIVER) },
            onClear = { terminalVm.clearLog(ProcessId.UNIVERSAL_RECEIVER) },
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun SshConnectionBar(
    connected: Boolean,
    connecting: Boolean,
    error: String?,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Box(
                    Modifier
                        .size(10.dp)
                        .clip(CircleShape)
                        .background(if (connected) StatusGreen else StatusRed)
                )
                Text(
                    text = if (connected) "SSH Connected"
                        else error ?: "SSH Disconnected",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            Button(
                onClick = if (connected) onDisconnect else onConnect,
                enabled = !connecting,
                colors = ButtonDefaults.buttonColors(containerColor = Gold),
            ) {
                if (connecting) {
                    CircularProgressIndicator(
                        Modifier.size(16.dp),
                        strokeWidth = 2.dp,
                        color = GoldDark,
                    )
                } else {
                    Text(
                        text = if (connected) "Disconnect" else "Connect",
                        color = GoldDark,
                    )
                }
            }
        }
    }
}

@Composable
private fun ProcessPanel(
    title: String,
    status: ProcessStatus,
    sshConnected: Boolean,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onClear: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val isRunning = status.state == ProcessState.RUNNING || status.state == ProcessState.STARTING
    val listState = rememberLazyListState()

    LaunchedEffect(status.lines.size) {
        if (status.lines.isNotEmpty()) {
            listState.animateScrollToItem(status.lines.lastIndex)
        }
    }

    Card(modifier = modifier.fillMaxWidth()) {
        Column(Modifier.fillMaxSize()) {
            // Header
            Row(
                modifier = Modifier.fillMaxWidth().padding(8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Box(
                        Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(
                                when (status.state) {
                                    ProcessState.RUNNING -> StatusGreen
                                    ProcessState.STARTING, ProcessState.STOPPING -> StatusYellow
                                    ProcessState.ERROR -> StatusRed
                                    ProcessState.STOPPED -> MaterialTheme.colorScheme.onSurfaceVariant
                                }
                            )
                    )
                    Text(title, style = MaterialTheme.typography.titleSmall, color = Gold)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    IconButton(onClick = onClear, modifier = Modifier.size(32.dp)) {
                        Icon(
                            Icons.Default.Delete,
                            "Clear",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                    if (isRunning) {
                        Button(
                            onClick = onStop,
                            colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
                        ) {
                            Icon(Icons.Default.Stop, null, Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Stop")
                        }
                    } else {
                        Button(
                            onClick = onStart,
                            enabled = sshConnected,
                            colors = ButtonDefaults.buttonColors(containerColor = Gold),
                        ) {
                            Icon(Icons.Default.PlayArrow, null, Modifier.size(16.dp), tint = GoldDark)
                            Spacer(Modifier.width(4.dp))
                            Text("Start", color = GoldDark)
                        }
                    }
                }
            }

            // Log area
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .background(Color(0xFF1A1A2E))
                    .padding(4.dp),
            ) {
                if (status.lines.isEmpty()) {
                    Text(
                        text = status.error ?: "No output yet",
                        color = if (status.error != null) StatusRed else Color.Gray,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        modifier = Modifier.padding(8.dp),
                    )
                } else {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize().horizontalScroll(rememberScrollState()),
                    ) {
                        items(status.lines) { line ->
                            Text(
                                text = line,
                                color = if (line.startsWith("[stderr]"))
                                    StatusRed.copy(alpha = 0.9f)
                                else
                                    Color(0xFFE0E0E0),
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                maxLines = 1,
                            )
                        }
                    }
                }
            }
        }
    }
}
