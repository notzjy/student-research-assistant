import com.studentassistant.bridge.BridgeConfig;

/** 在桌面JVM执行纯地址校验测试，不模拟手机通知或Android后台行为。 */
public final class ServerValidationTest {
    private static int checks = 0;

    /** 验证明文令牌只能发往明确的私网地址，同时拒绝凭证、路径和无效端口。 */
    public static void main(String[] args) throws Exception {
        accepts("http://192.168.1.8:8002", "http://192.168.1.8:8002");
        accepts("http://10.0.0.2/", "http://10.0.0.2");
        accepts("http://172.16.1.2:8002", "http://172.16.1.2:8002");
        accepts("http://172.31.255.1:8002", "http://172.31.255.1:8002");
        accepts("http://127.0.0.1:8002", "http://127.0.0.1:8002");
        accepts("https://assistant.example.org", "https://assistant.example.org");
        rejects("http://example.org"); rejects("http://8.8.8.8"); rejects("http://172.32.1.2");
        rejects("http://192.168.1.8:0"); rejects("http://192.168.1.8:65536");
        rejects("http://user:secret@192.168.1.8"); rejects("http://192.168.1.8/other");
        rejects("http://192.168.1.8?token=secret"); rejects("http://192.168.1.8#secret");
        rejects("file:///tmp/bridge"); rejects("http://127.0.0.1.example.org");
        rejects("http://192.168.256.1");
        System.out.println("Server URL validation passed: " + checks + " checks; no Android device test claimed.");
    }

    /** 合法地址应被规范化为唯一根地址。 */
    private static void accepts(String input, String expected) throws Exception {
        if (!expected.equals(BridgeConfig.validateServer(input))) throw new AssertionError("Address normalization failed");
        checks++;
    }

    /** 不合法地址必须拒绝，不能让明文鉴权被误发。 */
    private static void rejects(String input) throws Exception {
        try { BridgeConfig.validateServer(input); }
        catch (Exception expected) { checks++; return; }
        throw new AssertionError("Unsafe server address accepted");
    }
}
