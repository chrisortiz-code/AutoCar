package com.autocar.app.data.gamepad

import android.view.InputDevice
import android.view.KeyEvent
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

    // Track previous HAT values for edge detection
    private var prevHatX = 0f
    private var prevHatY = 0f

    fun onMotion(event: MotionEvent) {
        // Synthesize D-pad key presses from HAT axes (PS4/PS5 controllers)
        val hatX = event.getAxisValue(MotionEvent.AXIS_HAT_X)
        val hatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
        handleHatAxis(hatX, prevHatX, KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_DPAD_LEFT)
        handleHatAxis(hatY, prevHatY, KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_DPAD_UP)
        prevHatX = hatX
        prevHatY = hatY

        _state.value = _state.value.copy(
            leftX = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_X)),
            leftY = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_Y)),
            rightX = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_Z)),
            rightY = applyDeadzone(event.getAxisValue(MotionEvent.AXIS_RZ)),
            l2 = event.getAxisValue(MotionEvent.AXIS_LTRIGGER),
            r2 = event.getAxisValue(MotionEvent.AXIS_RTRIGGER),
            buttons = pressedButtons.toSet(),
        )
    }

    /** Convert a HAT axis value to synthetic button press/release. */
    private fun handleHatAxis(value: Float, prev: Float, posKey: Int, negKey: Int) {
        val cur = when {
            value > 0.5f -> 1
            value < -0.5f -> -1
            else -> 0
        }
        val old = when {
            prev > 0.5f -> 1
            prev < -0.5f -> -1
            else -> 0
        }
        if (cur == old) return
        // Release old direction
        if (old == 1) pressedButtons.remove(posKey)
        if (old == -1) pressedButtons.remove(negKey)
        // Press new direction
        if (cur == 1) pressedButtons.add(posKey)
        if (cur == -1) pressedButtons.add(negKey)
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
