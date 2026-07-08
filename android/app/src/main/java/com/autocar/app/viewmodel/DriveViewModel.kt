package com.autocar.app.viewmodel

import android.app.Application
import android.view.KeyEvent
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.api.ApiClient
import com.autocar.app.data.api.DriveApi
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.data.gamepad.GamepadState
import com.autocar.app.data.model.DriveStatus
import com.autocar.app.data.settings.SettingsStore
import com.autocar.app.data.websocket.SensorSocket
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class DriveViewModel(app: Application) : AndroidViewModel(app) {

    companion object {
        private const val MAX_TRANS_SPEED = 8f
        private const val MAX_ROT_SPEED = 3f
        private const val SEND_INTERVAL_MS = 50L // 20 Hz
    }

    private val store = SettingsStore(app)
    private var sensorSocket: SensorSocket? = null
    private var gamepadJob: Job? = null

    private val _driveStatus = MutableStateFlow(DriveStatus())
    val driveStatus: StateFlow<DriveStatus> = _driveStatus.asStateFlow()

    private val _wsConnected = MutableStateFlow(false)
    val wsConnected: StateFlow<Boolean> = _wsConnected.asStateFlow()

    fun connectWebSocket() = viewModelScope.launch {
        val baseUrl = store.baseUrl.first()
        val token = store.token.first()
        val client = ApiClient.okHttpClient(token)
        sensorSocket = SensorSocket(client)

        try {
            _wsConnected.value = true
            sensorSocket!!.connect(baseUrl).collect { update ->
                update.drive?.let { drive ->
                    _driveStatus.value = _driveStatus.value.copy(moving = drive.moving)
                }
                update.motors?.let { m ->
                    _driveStatus.value = _driveStatus.value.copy(
                        connected = m.connected,
                        armed = m.armed,
                        motors = m.motors,
                        receiver_online = m.receiver_online,
                        uptime = m.uptime,
                    )
                }
            }
        } catch (_: Exception) {
            _wsConnected.value = false
        }
    }

    fun startGamepadDriving(gamepadManager: GamepadManager) {
        gamepadJob?.cancel()
        gamepadJob = viewModelScope.launch {
            var prevSending = false
            while (true) {
                delay(SEND_INTERVAL_MS)
                val state = gamepadManager.state.value
                if (!state.connected) continue

                // Handle buttons
                handleButtons(state)

                val vx = -state.leftY  // stick up (negative) = forward (positive)
                val vy = state.leftX
                val rotSpeed = state.rightX * MAX_ROT_SPEED
                val r2Mapped = 10f + state.r2 * 90f // vel_pct 10-100
                val transSpeed = (r2Mapped / 100f) * MAX_TRANS_SPEED

                val isSending = vx != 0f || vy != 0f || rotSpeed != 0f
                if (isSending) {
                    sensorSocket?.sendDrive(vx, vy, transSpeed, rotSpeed)
                    prevSending = true
                } else if (prevSending) {
                    sensorSocket?.sendStop()
                    prevSending = false
                }
            }
        }
    }

    private fun handleButtons(state: GamepadState) {
        // Cross (A) = soft stop
        if (KeyEvent.KEYCODE_BUTTON_A in state.buttons) {
            sensorSocket?.sendStop()
        }
        // Circle (B) = estop
        if (KeyEvent.KEYCODE_BUTTON_B in state.buttons) {
            sensorSocket?.sendEstop()
        }
    }

    fun sendDrive(vx: Float, vy: Float, transSpeed: Float, rotSpeed: Float) {
        sensorSocket?.sendDrive(vx, vy, transSpeed, rotSpeed)
    }

    fun sendStop() {
        sensorSocket?.sendStop()
    }

    fun sendEstop() {
        sensorSocket?.sendEstop()
    }

    fun fetchStatus() = viewModelScope.launch {
        try {
            val baseUrl = store.baseUrl.first()
            val token = store.token.first()
            val api = ApiClient.get(baseUrl, token).create(DriveApi::class.java)
            _driveStatus.value = api.status()
        } catch (_: Exception) {}
    }

    override fun onCleared() {
        gamepadJob?.cancel()
        sensorSocket?.disconnect()
        super.onCleared()
    }
}
