package com.autocar.app

import android.hardware.input.InputManager
import android.os.Bundle
import android.util.Log
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.navigation.AppNavGraph
import com.autocar.app.ui.theme.AutoCarTheme
import com.autocar.app.ui.theme.ScaleLevels

class MainActivity : ComponentActivity(), InputManager.InputDeviceListener {

    val gamepadManager = GamepadManager()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val inputManager = getSystemService(INPUT_SERVICE) as InputManager
        inputManager.registerInputDeviceListener(this, null)
        gamepadManager.refreshDevices()

        setContent {
            var scaleLevel by remember { mutableIntStateOf(0) }
            val scaleFactor = ScaleLevels[scaleLevel]

            AutoCarTheme(scaleFactor = scaleFactor) {
                AppNavGraph(
                    gamepadManager = gamepadManager,
                    scaleLevel = scaleLevel,
                    onScaleCycle = { scaleLevel = (scaleLevel + 1) % ScaleLevels.size },
                )
            }
        }
    }

    override fun onGenericMotionEvent(event: MotionEvent): Boolean {
        if (event.source and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK) {
            gamepadManager.onMotion(event)
            return true
        }
        return super.onGenericMotionEvent(event)
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        Log.d("GamepadNav", "onKeyDown keyCode=$keyCode source=0x${event.source.toString(16)}" +
            " isGamepad=${isGamepadSource(event.source)}")
        if (isGamepadSource(event.source)) {
            gamepadManager.onKeyDown(keyCode)
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (isGamepadSource(event.source)) {
            gamepadManager.onKeyUp(keyCode)
            return true
        }
        return super.onKeyUp(keyCode, event)
    }

    private fun isGamepadSource(source: Int): Boolean {
        return source and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD ||
            source and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK
    }

    override fun onInputDeviceAdded(deviceId: Int) {
        gamepadManager.refreshDevices()
    }

    override fun onInputDeviceRemoved(deviceId: Int) {
        gamepadManager.refreshDevices()
    }

    override fun onInputDeviceChanged(deviceId: Int) {
        gamepadManager.refreshDevices()
    }

    override fun onDestroy() {
        val inputManager = getSystemService(INPUT_SERVICE) as InputManager
        inputManager.unregisterInputDeviceListener(this)
        super.onDestroy()
    }
}
