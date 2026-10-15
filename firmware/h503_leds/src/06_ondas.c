/*
 * 06_ondas — Variables que cambian en el tiempo, para la pestaña Gráficos del banco.
 *
 * Calcula cada 1 ms una senoidal, una triangular, una cuadrada y un contador, más
 * un "sensor" simulado (struct) y un array con las últimas muestras. En la web:
 * Gráficos → Variables del micro → elegir este .elf y las variables → Graficar.
 *
 * Los LEDs PA0..PA7 muestran la senoidal como una barra (0 a 8 LEDs encendidos).
 *
 * Mientras grafica se puede cambiar el programa en marcha desde Depurar o GDB:
 *     (gdb) set var frecuencia_hz = 2.5    -> la senoidal va más rápido
 *     (gdb) set var amplitud = 0.5
 */
#include "placa.h"

typedef struct {
    float    temperatura;   /* °C, varía lento alrededor de 25 */
    uint16_t adc;           /* 0..4095 */
    int8_t   estado;        /* -1, 0 o 1 según la tendencia */
} sensor_t;

volatile float    frecuencia_hz = 1.0f;     /* de la senoidal */
volatile float    amplitud = 1.0f;
volatile float    seno;                     /* amplitud * sin(2*pi*f*t) */
volatile float    triangular;               /* 0..100, 0,5 Hz */
volatile int16_t  cuadrada;                 /* +-1000, 2 Hz */
volatile uint32_t contador;                 /* +1 cada 100 ms */
volatile sensor_t sensor;
volatile int16_t  ultimas[8];               /* seno * 1000 de las últimas 8 décimas de segundo */

/* sin(pi*u) con u en [0, 1] (aproximación de Bhaskara, error < 0,2 %). */
static float medio_seno(float u)
{
    float p = u * (1.0f - u);
    return 16.0f * p / (5.0f - 4.0f * p);
}

/* sin(2*pi*x) para cualquier x (sólo importa la parte fraccionaria). */
static float seno_ciclo(float x)
{
    x -= (float)(int32_t)x;
    if (x < 0.0f) {
        x += 1.0f;
    }
    return x < 0.5f ? medio_seno(2.0f * x) : -medio_seno(2.0f * x - 1.0f);
}

/* Una función aparte para poder poner un breakpoint en cada actualización. */
__attribute__((noinline)) void ondas_paso(uint32_t ms)
{
    static float fase;                       /* integrada: cambiar la frecuencia no salta */
    fase += frecuencia_hz * 0.001f;
    if (fase >= 1.0f) {
        fase -= 1.0f;
    }
    seno = amplitud * seno_ciclo(fase);

    uint32_t t = ms % 2000U;                 /* 0,5 Hz */
    triangular = (t < 1000U ? (float)t : (float)(2000U - t)) * 0.1f;
    cuadrada = (ms % 500U) < 250U ? 1000 : -1000;

    if (ms % 100U == 0U) {
        contador++;
        for (int i = 7; i > 0; i--) {
            ultimas[i] = ultimas[i - 1];
        }
        ultimas[0] = (int16_t)(seno * 1000.0f);

        float anterior = sensor.temperatura;
        sensor.temperatura = 25.0f + 3.0f * seno_ciclo((float)ms * 0.00005f)   /* 20 s */
                             + 0.2f * seno_ciclo((float)ms * 0.0013f);
        sensor.adc = (uint16_t)(2048.0f + 2000.0f * seno_ciclo((float)ms * 0.0002f));
        sensor.estado = sensor.temperatura > anterior + 0.01f ? 1
                      : sensor.temperatura < anterior - 0.01f ? -1 : 0;
    }

    /* Barra en los LEDs: -1 -> ninguno, +1 -> los 8. */
    int encendidos = (int)((seno / (amplitud > 0.0f ? amplitud : 1.0f) + 1.0f) * 4.0f + 0.5f);
    leds_mostrar((uint8_t)((1U << (encendidos > 8 ? 8 : encendidos)) - 1U));
}

int main(void)
{
    placa_init();
    leds_mostrar(0x00);
    sensor.temperatura = 25.0f;

    uint32_t ultimo = ms_transcurridos;
    for (;;) {
        uint32_t ahora = ms_transcurridos;
        if (ahora != ultimo) {
            ultimo = ahora;
            ondas_paso(ahora);
        }
    }
}
