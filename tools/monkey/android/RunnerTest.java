package acpm.monkey;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

public final class RunnerTest {
    static void check(boolean condition) {
        if (!condition) throw new AssertionError();
    }

    public static void main(String[] args) throws Exception {
        Random a = new Random(42), b = new Random(42);
        Set<Integer> targets = new HashSet<>(Arrays.asList(2, 0));
        Set<List<Integer>> orders = new HashSet<>();
        for (int i=0;i<80;i++) {
            List<Integer> round = Runner.round(targets,a);
            check(round.size()==2 && new HashSet<>(round).equals(targets));
            check(round.equals(Runner.round(targets,b)));
            orders.add(round);
        }
        check(orders.size()==2);
        for(int action=0;action<4;action++) {
            Runner.Box box=new Runner.Box(400,200,540,400);
            if(action==3) check(Runner.points(new Runner.Box(0,0,2,2),a,action)==null);
            for(int i=0;i<100;i++) {
                int[][][] points=Runner.points(box,a,action);
                if(points!=null) for(int[][] frame:points) for(int[] p:frame) check(box.has(p[0],p[1]));
            }
        }

        String input="Input Dispatcher State:\n  Display: 2\n    Windows:\n"
            +"      0: name=spy, id=1, displayId=2, inputConfig=SPY, touchableRegion=[0,0][100,100], ownerUid=1000\n"
            +"      1: name=overlay, id=2, displayId=2, inputConfig=0x0, touchableRegion=[0,0][20,20], ownerUid=1000\n"
            +"      2: name=face test.app/.Page, id=3, displayId=2, inputConfig=0x0, applicationInfo.name=ActivityRecord{ab u0 test.app/.Page}, touchableRegion=[0,0][100,100], ownerUid=10300\n"
            +"  Display: 3\n    Windows:\n"
            +"      0: name=readonly, id=4, displayId=3, inputConfig=NOT_TOUCHABLE, touchableRegion=[0,0][100,100], ownerUid=1000";
        String wm="  Window #2 Window{face u0 test.app/.Page}:\n    mOwnerUid=10300 package=test.app appop=NONE";
        List<Runner.Window> windows=Runner.parseWindows(input,wm);
        check(windows.size()==2);
        String focus="Display: mDisplayId=2\nmCurrentFocus=Window{face u0 test.app/test.app.Page}\nmFocusedApp=null";
        Runner.Foreground foreground=Runner.foregroundActivities(focus,windows).get(2);
        check(foreground.user==0 && foreground.activity.equals("test.app/test.app.Page"));
        check(Runner.foregroundActivities(focus.replace("u0", "u10"),windows).isEmpty());
        check(Runner.foregroundActivities(focus.replace("face", "beef"),windows).isEmpty());
        check(Runner.foregroundActivities(focus.replace("Id=2", "Id=3"),windows).isEmpty());
        Runner.Screen screen=new Runner.Screen(2,0,100,100,0,2,"local:test","test.app/.Page");
        Map<Integer,Runner.Screen> baseline=Collections.singletonMap(2,screen);
        Runner.Snapshot snapshot=new Runner.Snapshot(baseline,windows);
        check(snapshot.hit(2,10,10).pkg.isEmpty()); // unknown top window blocks the app underneath
        check(snapshot.hit(2,40,50).pkg.equals("test.app"));
        check(snapshot.hit(3,40,50)==null);
        Runner.Target whitelist=new Runner.Target(2,new String[]{"screen","2","0","100","100","0","local:test","whitelist"});
        whitelist.packages.add("test.app");
        check(whitelist.eligible(windows.get(1),screen));
        Runner.Target blacklist=new Runner.Target(2,new String[]{"screen","2","0","100","100","0","local:test","blacklist"});
        check(blacklist.eligible(windows.get(1),screen));
        blacklist.packages.add("test.app");
        check(!blacklist.eligible(windows.get(1),screen));
        for(Runner.Screen changed:Arrays.asList(
                new Runner.Screen(2,1,100,100,0,2,"local:test","test.app/.Page"),
                new Runner.Screen(2,0,100,100,1,2,"local:test","test.app/.Page"))) {
            try {
                Runner.checkTopology(baseline,new Runner.Snapshot(Collections.singletonMap(2,changed),windows));
                throw new AssertionError("topology change was ignored");
            } catch (IllegalStateException expected) { }
        }
        checkAppSwitch();
        checkInjectionRejection();
        System.out.println("PASS: seeded coverage, gesture bounds, ownership, blacklist, topology, app switch and rejected input");
    }

    static void checkInjectionRejection() throws Exception {
        Runner.Screen screen=new Runner.Screen(2,0,100,100,0,2,"secondary","test.app/.Main");
        Runner.Window window=new Runner.Window(2,1000,"main","test.app",
            Collections.singletonList(new Runner.Box(0,0,100,100)),screen.activity);
        int[][][] tap={{{40,50}}};
        int[][][] swipe={{{40,50}},{{60,70}}};
        List<Integer> events=new ArrayList<>();
        Set<Integer> rejected=new HashSet<>();
        Set<Integer> mismatched=new HashSet<>();
        class FakeInputService {
            public boolean inject(int action,int mode,int uid) {
                check(mode==2 && uid==window.uid);
                if(mismatched.contains(action))
                    throw new IllegalArgumentException("Targeted input event injection from pid 26374"
                        +" was not directed at a window owned by uid "+uid+".");
                if(action==99) throw new IllegalArgumentException("mode is invalid");
                return !rejected.contains(action);
            }
        }
        FakeInputService service=new FakeInputService();
        Method inject=FakeInputService.class.getMethod("inject",int.class,int.class,int.class);
        Runner.GestureInput input=new Runner.GestureInput() {
            public long now() { return 100L; }
            public void verifyGeometry(Runner.Screen target) { check(target==screen); }
            public boolean emit(Runner.Screen target,int uid,long down,int action,int[][] points,boolean downPressure) throws Exception {
                check(target==screen && uid==window.uid && down==100L);
                if(action==3) check(!downPressure && Arrays.deepEquals(points,tap[0]));
                events.add(action);
                return Runner.injectTarget(inject,service,action,uid);
            }
        };
        Path directory=Files.createTempDirectory("acpm-rejected-input");
        Path halt=directory.resolve("halt");
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();
        PrintStream stdout=System.out;
        try(PrintStream capture=new PrintStream(bytes,true,"UTF-8")) {
            System.setOut(capture);
            rejected.add(0);
            Runner.performGesture(input,screen,window,83,1,tap,halt);
            check(events.equals(Collections.singletonList(0)));

            events.clear();
            rejected.clear();
            rejected.add(1);
            Runner.performGesture(input,screen,window,84,1,tap,halt);
            check(events.equals(Arrays.asList(0,1,3)));

            events.clear();
            rejected.clear();
            rejected.addAll(Arrays.asList(2,3));
            Runner.performGesture(input,screen,window,85,2,swipe,halt);
            check(events.equals(Arrays.asList(0,2,3)));

            events.clear();
            rejected.clear();
            mismatched.add(0);
            Runner.performGesture(input,screen,window,86,1,tap,halt);
            check(events.equals(Collections.singletonList(0)));

            events.clear();
            mismatched.clear();
            mismatched.addAll(Arrays.asList(1,3));
            Runner.performGesture(input,screen,window,87,1,tap,halt);
            check(events.equals(Arrays.asList(0,1,3)));

            events.clear();
            mismatched.clear();
            Runner.performGesture(input,screen,window,88,0,tap,halt);
            check(events.equals(Arrays.asList(0,1)));

            try {
                Runner.injectTarget(inject,service,99,window.uid);
                throw new AssertionError("unrelated injection exception was ignored");
            } catch(InvocationTargetException expected) {
                check(expected.getCause().getMessage().equals("mode is invalid"));
            }
        } finally {
            System.setOut(stdout);
            Files.delete(directory);
        }
        String[] results=bytes.toString("UTF-8").split("\\R");
        check(results.length==6);
        check(results[0].equals("@ACPM\tRESULT\t83\t2\tlong_press\tskipped\ttest.app\t定向输入被系统拒绝"));
        check(results[1].equals("@ACPM\tRESULT\t84\t2\tlong_press\tskipped\ttest.app\t定向输入被系统拒绝"));
        check(results[2].equals("@ACPM\tRESULT\t85\t2\tswipe\tskipped\ttest.app\t定向输入被系统拒绝"));
        check(results[3].equals("@ACPM\tRESULT\t86\t2\tlong_press\tskipped\ttest.app\t触摸窗口归属不匹配（目标 UID 1000）"));
        check(results[4].equals("@ACPM\tRESULT\t87\t2\tlong_press\tskipped\ttest.app\t触摸窗口归属不匹配（目标 UID 1000）"));
        check(results[5].equals("@ACPM\tRESULT\t88\t2\ttap\tsuccess\ttest.app\t[[[40, 50]]]"));
    }

    static void checkAppSwitch() throws Exception {
        Runner.Screen old=new Runner.Screen(0,0,100,100,0,2,"main","old.app/.Main");
        Runner.Screen other=new Runner.Screen(2,0,100,100,0,2,"secondary","other.app/.Main");
        Runner.Target target=new Runner.Target(0,new String[]{"screen","0","0","100","100","0","main","whitelist"});
        target.packages.add("video.app");
        target.activities.put("old.app","old.app/.Splash");
        target.activities.put("other.app","other.app/.Splash");
        target.activities.put("video.app","video.app/.Splash");

        Map<Integer,Runner.Screen> baseline=snapshot(old,other).screens;
        List<String> expected=Arrays.asList("am","start","--display","0","--user","0","-n","video.app/.Splash");
        Path directory=Files.createTempDirectory("acpm-switch");
        Path halt=directory.resolve("halt");
        Random seed=new Random(42);
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();
        PrintStream stdout=System.out;
        try(PrintStream capture=new PrintStream(bytes,true,"UTF-8")) {
            System.setOut(capture);
            Runner.switchApp(args->{
                check(Arrays.asList(args).equals(expected));
                return "Starting: Intent";
            },old,target,seed,baseline,127,halt);
            Runner.switchApp(args->"Error: Activity not started",old,target,seed,baseline,128,halt);
            Runner.switchApp(args->{ throw new Runner.CommandRejected("am"); },old,target,seed,baseline,129,halt);
        } finally {
            System.setOut(stdout);
            Files.delete(directory);
        }
        String[] results=bytes.toString("UTF-8").split("\\R");
        check(results.length==3 && results[0].contains("127\t0\tapp_switch\tsuccess\tvideo.app/.Splash\t已发出应用启动指令"));
        check(results[1].contains("128\t0\tapp_switch\tskipped"));
        check(results[2].contains("129\t0\tapp_switch\tskipped"));

        String controller="com.android.permissioncontroller";
        String dialog=controller+"/.permission.ui.GrantPermissionsActivity";
        Runner.Screen prompt=new Runner.Screen(0,0,100,100,0,2,"main",dialog);
        Runner.Screen handoff=new Runner.Screen(0,-1,100,100,0,2,"main","");
        Runner.Window application=new Runner.Window(0,10001,"video","video.app",
            Collections.singletonList(new Runner.Box(0,0,100,100)),"video.app/.Main");
        Runner.Window popup=new Runner.Window(0,10141,"prompt",controller,
            Collections.singletonList(new Runner.Box(0,0,50,100)),dialog);
        Runner.Window otherPopup=new Runner.Window(2,10141,"otherPrompt",controller,popup.boxes,dialog);
        Runner.Window wrongUserPopup=new Runner.Window(0,1010141,"wrongUserPrompt",controller,popup.boxes,dialog);
        Runner.Window controllerSettings=new Runner.Window(0,10141,"settings",controller,popup.boxes,controller+"/.Settings");
        Runner.Snapshot promptSnapshot=snapshot(prompt,other,otherPopup,popup,application);
        check(target.eligible(popup,prompt) && target.eligible(application,prompt));
        check(!target.eligible(otherPopup,prompt) && !target.eligible(wrongUserPopup,prompt));
        check(!target.eligible(controllerSettings,prompt));
        check(promptSnapshot.hit(0,10,40)==popup && promptSnapshot.hit(0,70,40)==application);

        Runner.Target blacklist=new Runner.Target(0,new String[]{"screen","0","0","100","100","0","main","blacklist"});
        blacklist.packages.add(controller);
        check(blacklist.eligible(popup,prompt) && blacklist.eligible(application,prompt));
        blacklist.packages.add("video.app");
        check(!blacklist.eligible(application,prompt));
        check(Runner.checkTopology(baseline,snapshot(handoff,other)).get(0).user==-1);
    }

    static Runner.Snapshot snapshot(Runner.Screen main,Runner.Screen other,Runner.Window... windows) {
        Map<Integer,Runner.Screen> screens=new TreeMap<>();
        screens.put(main.id,main);
        screens.put(other.id,other);
        return new Runner.Snapshot(screens,Arrays.asList(windows));
    }
}
