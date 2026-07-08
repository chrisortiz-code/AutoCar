package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.api.ApiClient
import com.autocar.app.data.api.SensorsApi
import com.autocar.app.data.model.SensorUpdate
import com.autocar.app.data.settings.SettingsStore
import com.autocar.app.data.websocket.SensorSocket
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class SensorsViewModel(app: Application) : AndroidViewModel(app) {

    private val store = SettingsStore(app)
    private var sensorSocket: SensorSocket? = null

    private val _sensorUpdate = MutableStateFlow(SensorUpdate())
    val sensorUpdate: StateFlow<SensorUpdate> = _sensorUpdate.asStateFlow()

    private val _cameraUrl = MutableStateFlow("")
    val cameraUrl: StateFlow<String> = _cameraUrl.asStateFlow()

    private val _depthUrl = MutableStateFlow("")
    val depthUrl: StateFlow<String> = _depthUrl.asStateFlow()

    init {
        // Set camera URLs eagerly so they're available before SensorViewport is composed
        viewModelScope.launch {
            val baseUrl = store.baseUrl.first()
            _cameraUrl.value = baseUrl.trimEnd('/') + "/api/sensors/camera/rgb"
            _depthUrl.value = baseUrl.trimEnd('/') + "/api/sensors/camera/depth"
        }
    }

    fun connect() = viewModelScope.launch {
        val baseUrl = store.baseUrl.first()
        val token = store.token.first()

        _cameraUrl.value = baseUrl.trimEnd('/') + "/api/sensors/camera/rgb"
        _depthUrl.value = baseUrl.trimEnd('/') + "/api/sensors/camera/depth"

        val client = ApiClient.okHttpClient(token)
        sensorSocket = SensorSocket(client)

        try {
            sensorSocket!!.connect(baseUrl).collect { update ->
                _sensorUpdate.value = update
            }
        } catch (_: Exception) {}
    }

    fun fetchStatus() = viewModelScope.launch {
        try {
            val baseUrl = store.baseUrl.first()
            val token = store.token.first()
            val api = ApiClient.get(baseUrl, token).create(SensorsApi::class.java)
            val status = api.status()
            _sensorUpdate.value = _sensorUpdate.value.copy(
                lidar = com.autocar.app.data.model.LidarWsData(
                    connected = status.lidar.connected,
                    scan_hz = status.lidar.scan_hz,
                ),
                camera = com.autocar.app.data.model.CameraWsData(
                    connected = status.camera.connected,
                    fps = status.camera.fps,
                    resolution = status.camera.resolution,
                ),
            )
        } catch (_: Exception) {}
    }

    override fun onCleared() {
        sensorSocket?.disconnect()
        super.onCleared()
    }
}
