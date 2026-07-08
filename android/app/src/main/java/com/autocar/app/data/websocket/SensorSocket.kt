package com.autocar.app.data.websocket

import com.autocar.app.data.model.SensorUpdate
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

class SensorSocket(
    private val client: OkHttpClient,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private var webSocket: WebSocket? = null

    fun connect(baseUrl: String): Flow<SensorUpdate> = callbackFlow {
        val wsUrl = baseUrl
            .replace("http://", "ws://")
            .replace("https://", "wss://")
            .trimEnd('/') + "/api/ws/sensors"

        val request = Request.Builder().url(wsUrl).build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val update = json.decodeFromString<SensorUpdate>(text)
                    trySend(update)
                } catch (_: Exception) {}
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                close(t)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                close()
            }
        })

        awaitClose {
            webSocket?.close(1000, "bye")
            webSocket = null
        }
    }

    fun send(message: String) {
        webSocket?.send(message)
    }

    fun sendDrive(vx: Float, vy: Float, transSpeed: Float, rotSpeed: Float) {
        send("""{"type":"drive","vx":$vx,"vy":$vy,"trans_speed":$transSpeed,"rot_speed":$rotSpeed}""")
    }

    fun sendStop() {
        send("""{"type":"stop"}""")
    }

    fun sendEstop() {
        send("""{"type":"estop"}""")
    }

    fun disconnect() {
        webSocket?.close(1000, "bye")
        webSocket = null
    }
}
