package com.autocar.app

import android.hardware.input.InputManager
import android.os.Bundle
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.navigation.AppNavGraph
import com.autocar.app.ui.theme.AutoCarTheme

class MainActivity : ComponentActivity(), InputManager.InputDeviceListener {

    val gamepadManager = GamepadManager()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val inputManager = getSystemService(INPUT_SERVICE) as InputManager
        inputManager.registerInputDeviceListener(this, null)
        gamepadManager.refreshDevices()

        setContent {
            AutoCarTheme {
                AppNavGraph(gamepadManager = gamepadManager)
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
        if (event.source and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD) {
            gamepadManager.onKeyDown(keyCode)
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (event.source and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD) {
            gamepadManager.onKeyUp(keyCode)
            return true
        }
        return super.onKeyUp(keyCode, event)
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
