package com.autocar.app.data.gamepad

import android.view.InputDevice
import android.view.MotionEvent
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.abs

data class GamepadState(
    val connected: Boolean = false,
    val leftX: Float = 0f,
    val leftY: Float = 0f,
    val rightX: Float = 0f,
    val rightY: Float = 0f,
    val r2: Float = 0f,
    val l2: Float = 0f,
    val buttons: Set<Int> = emptySet(),
)

class GamepadManager {

    companion object {
        private const val DEADZONE = 0.12f
    }

    private val _state = MutableStateFlow(GamepadState())
    val state: StateFlow<GamepadState> = _state.asStateFlow()

    private val pressedButtons = mutableSetOf<Int>()

    fun onMotion(event: MotionEvent) {
        _state.value = _state.value.copy(
            leftX = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_X)),
            leftY = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_Y)),
            rightX = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_Z)),
            rightY = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_RZ)),
            l2 = event.getAxisValue(MotionEvent.AXIS_LTRIGGER),
            r2 = event.getAxisValue(MotionEvent.AXIS_RTRIGGER),
        )
    }

    fun onKeyDown(keyCode: Int) {
        pressedButtons.add(keyCode)
        _state.value = _state.value.copy(buttons = pressedButtons.toSet())
    }

    fun onKeyUp(keyCode: Int) {
        pressedButtons.remove(keyCode)
        _state.value = _state.value.copy(buttons = pressedButtons.toSet())
    }

    fun refreshDevices() {
        val ids = InputDevice.getDeviceIds()
        val hasGamepad = ids.any { id ->
            val device = InputDevice.getDevice(id)
            device != null && device.sources and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD
        }
        _state.value = _state.value.copy(connected = hasGamepad)
    }

    private fun applyDeadzone(value: Float): Float {
        return if (abs(value) < DEADZONE) 0f else value
    }
}
