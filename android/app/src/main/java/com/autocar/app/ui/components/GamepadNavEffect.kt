package com.autocar.app.ui.components

import android.util.Log
import android.view.KeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Popup
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.navigation.NavTab
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.GoldDark
import com.autocar.app.ui.theme.LocalUiScale
import com.autocar.app.viewmodel.DriveViewModel
import com.autocar.app.viewmodel.FaceViewModel
import com.autocar.app.viewmodel.PathsViewModel
import com.autocar.app.viewmodel.TrackViewModel
import kotlinx.coroutines.delay

private const val TAG = "GamepadNav"
private const val POLL_MS = 50L

/**
 * Composable effect that maps gamepad button presses to app navigation and
 * context-dependent actions. Uses edge detection so actions fire once per press.
 * Polling loop at 20 Hz — same proven pattern as the drive loop.
 */
@Composable
fun GamepadNavEffect(
    gamepadManager: GamepadManager,
    currentTab: NavTab,
    onTabChange: (NavTab) -> Unit,
    faceBackend: String,
    onFaceBackendChange: (String) -> Unit,
    faceMode: String,
    onFaceModeChange: (String) -> Unit,
    faceVm: FaceViewModel,
    trackBackend: String,
    onTrackBackendChange: (String) -> Unit,
    trackMode: String,
    onTrackModeChange: (String) -> Unit,
    trackVm: TrackViewModel,
    driveVm: DriveViewModel,
    pathsVm: PathsViewModel,
    pathsSelectedIndex: Int,
    onPathsSelectedIndexChange: (Int) -> Unit,
    pathsCount: Int,
    onScaleCycle: () -> Unit = {},
    onAnyButtonPress: () -> Unit = {},
    onSensorsGridNav: ((delta: Int) -> Unit)? = null,
    onSensorsPanelCycle: ((delta: Int) -> Unit)? = null,
) {
    val tabs = NavTab.entries.toList()
    val faceBackends = listOf("mediapipe", "yolo", "yunet", "scrfd")
    val trackBackends = listOf("orb", "akaze", "sift", "lightglue")

    // Keep references fresh inside the long-lived LaunchedEffect
    val curTab by rememberUpdatedState(currentTab)
    val curOnTabChange by rememberUpdatedState(onTabChange)
    val curFaceBackend by rememberUpdatedState(faceBackend)
    val curOnFaceBackendChange by rememberUpdatedState(onFaceBackendChange)
    val curFaceMode by rememberUpdatedState(faceMode)
    val curOnFaceModeChange by rememberUpdatedState(onFaceModeChange)
    val curTrackBackend by rememberUpdatedState(trackBackend)
    val curOnTrackBackendChange by rememberUpdatedState(onTrackBackendChange)
    val curTrackMode by rememberUpdatedState(trackMode)
    val curOnTrackModeChange by rememberUpdatedState(onTrackModeChange)
    val curPathsSelectedIndex by rememberUpdatedState(pathsSelectedIndex)
    val curOnPathsSelectedIndexChange by rememberUpdatedState(onPathsSelectedIndexChange)
    val curPathsCount by rememberUpdatedState(pathsCount)
    val curOnScaleCycle by rememberUpdatedState(onScaleCycle)
    val curOnAnyButtonPress by rememberUpdatedState(onAnyButtonPress)
    val curOnSensorsGridNav by rememberUpdatedState(onSensorsGridNav)
    val curOnSensorsPanelCycle by rememberUpdatedState(onSensorsPanelCycle)

    LaunchedEffect(Unit) {
        var prevButtons = emptySet<Int>()
        var r3PressStartMs = 0L      // when R3 was first held
        var r3Activated = false       // true once the 5s hold fires (prevent re-fire)
        Log.d(TAG, "GamepadNavEffect polling started")
        while (true) {
            delay(POLL_MS)
            val state = gamepadManager.state.value
            if (!state.connected) {
                prevButtons = emptySet()
                r3PressStartMs = 0L
                r3Activated = false
                driveVm.updateR3HoldProgress(0f)
                continue
            }

            // ── R3 hold / tap detection (runs every tick, not edge-only) ──
            val r3Held = KeyEvent.KEYCODE_BUTTON_THUMBR in state.buttons
            val r3JustPressed = KeyEvent.KEYCODE_BUTTON_THUMBR in (state.buttons - prevButtons)
            val r3JustReleased = KeyEvent.KEYCODE_BUTTON_THUMBR in (prevButtons - state.buttons)
            val driving = driveVm.gamepadDriving.value

            if (r3JustPressed) {
                r3PressStartMs = System.currentTimeMillis()
                r3Activated = false
            }

            if (r3Held && !driving && r3PressStartMs > 0L && !r3Activated) {
                val elapsed = System.currentTimeMillis() - r3PressStartMs
                val progress = (elapsed.toFloat() / DriveViewModel.R3_HOLD_DURATION_MS).coerceIn(0f, 1f)
                driveVm.updateR3HoldProgress(progress)
                if (elapsed >= DriveViewModel.R3_HOLD_DURATION_MS) {
                    driveVm.activateGamepadDriving()
                    r3Activated = true
                    Log.d(TAG, "R3 hold complete — controller drive ACTIVATED")
                }
            }

            if (r3JustReleased) {
                if (driving && !r3Activated) {
                    // Quick tap while driving → deactivate
                    driveVm.deactivateGamepadDriving()
                    Log.d(TAG, "R3 tap — controller drive DEACTIVATED")
                }
                // Reset hold state
                r3PressStartMs = 0L
                r3Activated = false
                driveVm.updateR3HoldProgress(0f)
            }

            val newPresses = state.buttons - prevButtons
            prevButtons = state.buttons

            if (newPresses.isEmpty()) continue

            Log.d(TAG, "New presses: $newPresses on tab: $curTab")

            // Notify any-button-press for auto-hide reset
            curOnAnyButtonPress()

            for (button in newPresses) {
                when (button) {
                    // ── R3 handled above ──
                    KeyEvent.KEYCODE_BUTTON_THUMBR -> {}

                    // ── Global: L3 (left stick click) = cycle UI scale ──
                    KeyEvent.KEYCODE_BUTTON_THUMBL -> curOnScaleCycle()

                    // ── Global: D-pad left/right switches tabs ──
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        val idx = tabs.indexOf(curTab)
                        val newIdx = if (idx > 0) idx - 1 else tabs.lastIndex
                        curOnTabChange(tabs[newIdx])
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        val idx = tabs.indexOf(curTab)
                        val newIdx = if (idx < tabs.lastIndex) idx + 1 else 0
                        curOnTabChange(tabs[newIdx])
                    }

                    // ── Global: Start (Options) = context start ──
                    KeyEvent.KEYCODE_BUTTON_START -> {
                        when (curTab) {
                            NavTab.Face -> {
                                if (faceVm.faceState.value.status == "idle") {
                                    faceVm.start(
                                        backend = curFaceBackend,
                                        follow = curFaceMode == "follow",
                                    )
                                }
                            }
                            NavTab.Track -> {
                                if (trackVm.trackState.value.status == "idle") {
                                    trackVm.start(
                                        backend = curTrackBackend,
                                        follow = curTrackMode == "follow",
                                    )
                                }
                            }
                            else -> {}
                        }
                    }

                    // ── Global: Select (Share) = context stop ──
                    KeyEvent.KEYCODE_BUTTON_SELECT -> {
                        when (curTab) {
                            NavTab.Face -> faceVm.stop()
                            NavTab.Track -> trackVm.stop()
                            NavTab.Drive -> driveVm.sendStop()
                            NavTab.Paths -> {
                                val pid = pathsVm.status.value.path_id
                                if (pid != null) pathsVm.stopExecution(pid)
                            }
                            else -> {}
                        }
                    }

                    // ── L1 / R1: cycle backends / panel tabs ──
                    KeyEvent.KEYCODE_BUTTON_L1 -> {
                        when (curTab) {
                            NavTab.Face -> {
                                if (faceVm.faceState.value.status == "idle") {
                                    val idx = faceBackends.indexOf(curFaceBackend)
                                    val newIdx = if (idx > 0) idx - 1 else faceBackends.lastIndex
                                    curOnFaceBackendChange(faceBackends[newIdx])
                                }
                            }
                            NavTab.Track -> {
                                if (trackVm.trackState.value.status == "idle") {
                                    val idx = trackBackends.indexOf(curTrackBackend)
                                    val newIdx = if (idx > 0) idx - 1 else trackBackends.lastIndex
                                    curOnTrackBackendChange(trackBackends[newIdx])
                                }
                            }
                            NavTab.Sensors -> curOnSensorsPanelCycle?.invoke(-1)
                            else -> {}
                        }
                    }
                    KeyEvent.KEYCODE_BUTTON_R1 -> {
                        when (curTab) {
                            NavTab.Face -> {
                                if (faceVm.faceState.value.status == "idle") {
                                    val idx = faceBackends.indexOf(curFaceBackend)
                                    val newIdx = if (idx < faceBackends.lastIndex) idx + 1 else 0
                                    curOnFaceBackendChange(faceBackends[newIdx])
                                }
                            }
                            NavTab.Track -> {
                                if (trackVm.trackState.value.status == "idle") {
                                    val idx = trackBackends.indexOf(curTrackBackend)
                                    val newIdx = if (idx < trackBackends.lastIndex) idx + 1 else 0
                                    curOnTrackBackendChange(trackBackends[newIdx])
                                }
                            }
                            NavTab.Sensors -> curOnSensorsPanelCycle?.invoke(1)
                            else -> {}
                        }
                    }

                    // ── Y (Triangle): toggle mode ──
                    KeyEvent.KEYCODE_BUTTON_Y -> {
                        when (curTab) {
                            NavTab.Face -> {
                                if (faceVm.faceState.value.status == "idle") {
                                    curOnFaceModeChange(if (curFaceMode == "trace") "follow" else "trace")
                                }
                            }
                            NavTab.Track -> {
                                if (trackVm.trackState.value.status == "idle") {
                                    curOnTrackModeChange(if (curTrackMode == "trace") "follow" else "trace")
                                }
                            }
                            else -> {}
                        }
                    }

                    // ── A (Cross): context confirm/execute ──
                    KeyEvent.KEYCODE_BUTTON_A -> {
                        when (curTab) {
                            NavTab.Face -> {
                                if (faceVm.faceState.value.status == "confirming") {
                                    faceVm.confirmFace()
                                }
                            }
                            NavTab.Drive -> driveVm.sendStop()
                            NavTab.Paths -> {
                                val paths = pathsVm.paths.value
                                if (curPathsSelectedIndex in paths.indices) {
                                    pathsVm.executePath(paths[curPathsSelectedIndex].id)
                                }
                            }
                            else -> {}
                        }
                    }

                    // ── B (Circle): context reset/estop ──
                    KeyEvent.KEYCODE_BUTTON_B -> {
                        when (curTab) {
                            NavTab.Face -> {
                                val s = faceVm.faceState.value.status
                                if (s != "idle" && s != "loading") faceVm.reset()
                            }
                            NavTab.Track -> {
                                val s = trackVm.trackState.value.status
                                if (s != "idle" && s != "loading") trackVm.reset()
                            }
                            NavTab.Drive -> driveVm.sendEstop()
                            NavTab.Paths -> {
                                val pid = pathsVm.status.value.path_id
                                if (pid != null) pathsVm.stopExecution(pid)
                            }
                            else -> {}
                        }
                    }

                    // ── D-pad up/down: paths list / sensors grid navigation ──
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        when (curTab) {
                            NavTab.Paths -> {
                                if (curPathsCount > 0) {
                                    val newIdx = if (curPathsSelectedIndex > 0) curPathsSelectedIndex - 1 else curPathsCount - 1
                                    curOnPathsSelectedIndexChange(newIdx)
                                }
                            }
                            NavTab.Sensors -> curOnSensorsGridNav?.invoke(-1)
                            else -> {}
                        }
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        when (curTab) {
                            NavTab.Paths -> {
                                if (curPathsCount > 0) {
                                    val newIdx = if (curPathsSelectedIndex < curPathsCount - 1) curPathsSelectedIndex + 1 else 0
                                    curOnPathsSelectedIndexChange(newIdx)
                                }
                            }
                            NavTab.Sensors -> curOnSensorsGridNav?.invoke(1)
                            else -> {}
                        }
                    }
                }
            }
        }
    }
}

// ── Controls info popup ──

@Composable
fun GamepadControlsButton(currentTab: NavTab) {
    var showPopup by remember { mutableStateOf(false) }

    val scale = LocalUiScale.current
    Box {
        IconButton(
            onClick = { showPopup = true },
            modifier = Modifier
                .size((40 * scale).dp)
                .background(GoldDark.copy(alpha = 0.5f), CircleShape),
        ) {
            Icon(
                Icons.Default.SportsEsports,
                contentDescription = "Controls",
                tint = Gold,
                modifier = Modifier.size((22 * scale).dp),
            )
        }

        if (showPopup) {
            Popup(
                alignment = Alignment.BottomStart,
                onDismissRequest = { showPopup = false },
            ) {
                // Dismiss scrim
                Box(modifier = Modifier.fillMaxSize()) {
                    // Invisible tap-catcher to dismiss
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .clickable(
                                indication = null,
                                interactionSource = remember { MutableInteractionSource() },
                            ) { showPopup = false },
                    )
                    // Popup card anchored bottom-left
                    Surface(
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .padding(start = 8.dp, bottom = 52.dp),
                        shape = RoundedCornerShape(12.dp),
                        color = MaterialTheme.colorScheme.surface,
                        tonalElevation = 8.dp,
                        shadowElevation = 8.dp,
                    ) {
                        ControlsContent(currentTab)
                    }
                }
            }
        }
    }
}

@Composable
private fun ControlsContent(currentTab: NavTab) {
    Column(
        modifier = Modifier.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            "Controller — ${currentTab.label}",
            style = MaterialTheme.typography.titleMedium,
            color = Gold,
            fontWeight = FontWeight.Bold,
        )

        // Global controls
        ControlRow("D-pad L/R", "Switch tabs")
        ControlRow("Options", "Start")
        ControlRow("Share", "Stop")
        ControlRow("L3", "Cycle UI scale")

        // Screen-specific
        when (currentTab) {
            NavTab.Face -> {
                SectionLabel("Idle")
                ControlRow("L1 / R1", "Cycle backend")
                ControlRow("Triangle", "Toggle Trace/Follow")
                SectionLabel("Active")
                ControlRow("Cross", "Confirm face")
                ControlRow("Circle", "Reset tracking")
            }
            NavTab.Track -> {
                SectionLabel("Idle")
                ControlRow("L1 / R1", "Cycle backend")
                ControlRow("Triangle", "Toggle Trace/Follow")
                SectionLabel("Active")
                ControlRow("Circle", "Reset tracker")
            }
            NavTab.Drive -> {
                ControlRow("R3 hold 5s", "Activate driving")
                ControlRow("R3 tap", "Deactivate driving")
                SectionLabel("Active")
                ControlRow("L stick", "Move")
                ControlRow("R stick", "Rotate")
                ControlRow("R2", "Speed")
                ControlRow("Cross", "Soft stop")
                ControlRow("Circle", "E-stop")
            }
            NavTab.Paths -> {
                ControlRow("D-pad U/D", "Select path")
                ControlRow("Cross", "Execute")
                ControlRow("Circle", "Stop")
            }
            NavTab.Config -> {
                Text(
                    "No specific controls",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            NavTab.Sensors -> {
                ControlRow("D-pad U/D", "Cycle feeds")
                ControlRow("L1 / R1", "Cycle panel")
            }
            NavTab.Color -> {
                ControlRow("Triangle", "Toggle Trace/Follow")
            }
        }
    }
}

@Composable
private fun ControlRow(button: String, action: String) {
    val scale = LocalUiScale.current
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            button,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Bold,
            color = Gold,
            fontSize = (13 * scale).sp,
            modifier = Modifier.padding(end = 24.dp),
        )
        Text(
            action,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface,
            fontSize = (13 * scale).sp,
        )
    }
}

@Composable
private fun SectionLabel(label: String) {
    Text(
        label,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp),
    )
}
