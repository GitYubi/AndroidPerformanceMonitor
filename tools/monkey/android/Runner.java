package acpm.monkey;

import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

public final class Runner {
    static final String[] ACTIONS = {"tap", "long_press", "swipe", "pinch", "app_switch"};
    static final Pattern ACTIVITY = Pattern.compile("mFocusedApp=ActivityRecord\\{[^\\n]*?\\bu(\\d+)\\s+([\\w.$]+/[\\w.$]+)");
    static final Pattern CURRENT_FOCUS = Pattern.compile("mCurrentFocus=Window\\{([0-9a-f]+) u(\\d+) ([\\w.$]+/[\\w.$]+)\\}");
    static final Pattern DISPLAY = Pattern.compile("^\\s*Display:\\s*mDisplayId=(\\d+)\\b", Pattern.MULTILINE);
    static final Pattern WM_WINDOW = Pattern.compile("(?m)^\\s*Window #\\d+ Window\\{([0-9a-f]+) u(\\d+) ([^}]+)\\}:");
    static final Pattern INPUT_WINDOW = Pattern.compile("^\\s*\\d+: name=(.+?), (?:id=\\d+, )?displayId=(\\d+), (.*)$");
    static final Pattern RECT = Pattern.compile("\\[(-?\\d+),(-?\\d+)\\]\\[(-?\\d+),(-?\\d+)\\]");
    static final Pattern PACKAGE = Pattern.compile("[A-Za-z_][\\w]*(?:\\.[A-Za-z_][\\w]*)*");
    static final Pattern COMPONENT = Pattern.compile("[\\w.$]+/[\\w.$]+");

    static final class Box {
        final int l, t, r, b;
        Box(int l, int t, int r, int b) { this.l=l; this.t=t; this.r=r; this.b=b; }
        Box intersect(Box other) {
            return new Box(Math.max(l,other.l), Math.max(t,other.t), Math.min(r,other.r), Math.min(b,other.b));
        }
        boolean valid() { return l < r && t < b; }
        boolean has(int x, int y) { return l <= x && x < r && t <= y && y < b; }
    }

    static final class Screen {
        final int id, user, width, height, rotation, state;
        final String identity, activity;
        Screen(int id,int user,int width,int height,int rotation,int state,String identity,String activity) {
            this.id=id; this.user=user; this.width=width; this.height=height; this.rotation=rotation;
            this.state=state; this.identity=identity; this.activity=activity;
        }
        boolean sameDisplay(Screen s) {
            return s != null && id == s.id && width == s.width && height == s.height
                && rotation == s.rotation && state == s.state && identity.equals(s.identity);
        }
    }

    static final class Window {
        final int display, uid;
        final String token, pkg, activity;
        final List<Box> boxes;
        Window(int display,int uid,String token,String pkg,List<Box> boxes,String activity) {
            this.display=display; this.uid=uid; this.token=token; this.pkg=pkg; this.boxes=boxes;
            this.activity=activity;
        }
        boolean has(int x,int y) {
            for (Box b:boxes) if (b.has(x,y)) return true;
            return false;
        }
    }

    static final class Snapshot {
        final Map<Integer,Screen> screens;
        final List<Window> windows;
        Snapshot(Map<Integer,Screen> screens,List<Window> windows) { this.screens=screens; this.windows=windows; }
        Window hit(int display,int x,int y) {
            for (Window w:windows) if (w.display==display && w.has(x,y)) return w;
            return null;
        }

    }

    static boolean isPermissionPrompt(String activity) {
        return (activity.startsWith("com.android.permissioncontroller/")
            || activity.startsWith("com.google.android.permissioncontroller/"))
            && activity.endsWith(".permission.ui.GrantPermissionsActivity");
    }

    static final class Target {
        final int id;
        final Set<String> packages = new TreeSet<>();
        final Set<String> launches = new TreeSet<>();
        final Map<String,String> activities = new TreeMap<>();
        final Map<String,List<Box>> areas = new TreeMap<>();
        final String[] expected;
        final boolean blacklist;
        Target(int id,String[] expected) { this.id=id; this.expected=expected; this.blacklist=expected[7].equals("blacklist"); }
        boolean app(Window w,Screen s) {
            return !w.activity.isEmpty() && w.uid/100000==s.user && (blacklist
                ? !packages.contains(w.pkg) && (s.activity.startsWith(w.pkg+"/") || isPermissionPrompt(s.activity))
                : packages.contains(w.pkg));
        }
        boolean permissionPrompt(Window w,Screen s) {
            return w.uid/100000==s.user && isPermissionPrompt(s.activity) && w.activity.startsWith(w.pkg+"/")
                && normalize(w.activity).equals(normalize(s.activity));
        }
        boolean eligible(Window w,Screen s) {
            if(w.display!=id || w.uid<0 || w.pkg.isEmpty()) return false;
            if(permissionPrompt(w,s)) return true;
            return !(blacklist && packages.contains(w.pkg)) && (app(w,s) || areas.containsKey(w.pkg));
        }
        List<Box> limits(Window w, Screen screen) {
            List<Box> result = new ArrayList<>();
            Box display = new Box(0,0,screen.width,screen.height);
            if (app(w,screen) || permissionPrompt(w,screen)) result.add(display);
            result.addAll(areas.getOrDefault(w.pkg, Collections.emptyList()));
            return result;
        }
    }

    static final class Config {
        int seed, throttle;
        int[] ratios;
        final Map<Integer,Target> targets = new TreeMap<>();
        static Config read(String path) throws IOException {
            Config c = new Config();
            for (String line:Files.readAllLines(Paths.get(path),StandardCharsets.UTF_8)) {
                String[] f=line.split("\\t",-1);
                switch(f[0]) {
                    case "options":
                        c.seed=Integer.parseInt(f[1]); c.throttle=Integer.parseInt(f[2]);
                        c.ratios=new int[ACTIONS.length];
                        for(int i=0;i<ACTIONS.length;i++) c.ratios[i]=Integer.parseInt(f[i+3]);
                        break;
                    case "screen":
                        c.targets.put(Integer.parseInt(f[1]),new Target(Integer.parseInt(f[1]),f));
                        break;
                    case "app":
                        c.targets.get(Integer.parseInt(f[1])).packages.add(f[2]);
                        break;
                    case "launch":
                        c.targets.get(Integer.parseInt(f[1])).launches.add(f[2]);
                        break;
                    case "area":
                        c.targets.get(Integer.parseInt(f[1])).areas.computeIfAbsent(f[2],k->new ArrayList<>())
                            .add(new Box(Integer.parseInt(f[3]),Integer.parseInt(f[4]),Integer.parseInt(f[5]),Integer.parseInt(f[6])));
                        break;
                    default: throw new IOException("配置格式无效");
                }
            }
            return c;
        }
    }

    // Use z-ordered touchable regions from InputDispatcher, and identify the window owner by its WM token.
    static List<Window> parseWindows(String input, String windowManager) {
        String[] chunks=WM_WINDOW.split(windowManager);
        Matcher headers=WM_WINDOW.matcher(windowManager);
        Map<String,String> owners=new HashMap<>();
        int index=1;
        while(headers.find()) {
            Matcher owner=Pattern.compile("(?m)^\\s*mOwnerUid=(\\d+)\\b[^\\n]*?\\bpackage=([\\w.]+)").matcher(chunks[index++]);
            if(owner.find()) owners.put(headers.group(1)+":"+owner.group(1),owner.group(2));
        }
        List<Window> result=new ArrayList<>();
        boolean dispatcher=false, windows=false;
        for(String line:input.split("\\R")) {
            String stripped=line.trim();
            if(stripped.equals("Input Dispatcher State:")) dispatcher=true;
            if(!dispatcher) continue;
            if(stripped.equals("Windows:")) { windows=true; continue; }
            if(stripped.startsWith("Display:") || stripped.startsWith("Connections:")
                    || stripped.startsWith("RecentQueue:") || stripped.startsWith("PendingEvent:")) windows=false;
            if(!windows) continue;
            Matcher m=INPUT_WINDOW.matcher(line);
            if(!m.matches()) continue;
            String data=m.group(3);
            String flags=field(data,"inputConfig");
            if(flags.isEmpty()) {
                String bits=field(data,"flags");
                if(bits.isEmpty()) throw new IllegalStateException("输入窗口缺少 flags");
                if(!field(data,"visible").equals("true") || (Long.decode(bits)&16)!=0) continue;
            } else if(flags.contains("NOT_VISIBLE") || flags.contains("NOT_TOUCHABLE") || flags.contains("SPY")) {
                continue;
            }
            String uidField=field(data,"ownerUid");
            if(uidField.isEmpty()) throw new IllegalStateException("输入窗口缺少 UID");
            int uid=Integer.parseInt(uidField);
            Matcher region=RECT.matcher(field(data,"touchableRegion"));
            List<Box> boxes=new ArrayList<>();
            while(region.find()) boxes.add(new Box(Integer.parseInt(region.group(1)),Integer.parseInt(region.group(2)),
                Integer.parseInt(region.group(3)),Integer.parseInt(region.group(4))));
            if(boxes.isEmpty()) continue;
            String name=m.group(1);
            String token=name.replaceFirst("^'?(?:Window\\{)?([0-9a-f]+)\\s.*$","$1");
            String pkg=owners.getOrDefault(token+":"+uid,"");
            Matcher component=ACTIVITY.matcher("mFocusedApp="+field(data,"applicationInfo.name"));
            String activity=component.find()?component.group(2):"";
            if(pkg.isEmpty() && !activity.isEmpty()) pkg=activity.split("/",2)[0];
            // Keep unknown and non-dispatchable windows in the hit map so they cannot be tapped through.
            if(flags.contains("NO_INPUT_CHANNEL") || field(data,"paused").equals("true")) pkg="";
            result.add(new Window(Integer.parseInt(m.group(2)),uid,token,pkg,boxes,activity));
        }
        if(!dispatcher || !input.contains("Windows:")) throw new IllegalStateException("无法读取 InputDispatcher 窗口");
        return result;
    }

    static final class Foreground {
        final int user;
        final String activity;
        Foreground(int user,String activity) { this.user=user; this.activity=activity; }
    }

    static Map<Integer,Foreground> foregroundActivities(String wm,List<Window> windows) {
        String[] sections=DISPLAY.split(wm);
        Matcher header=DISPLAY.matcher(wm);
        Map<Integer,Foreground> foreground=new HashMap<>();
        for(int i=1;header.find();i++) {
            int id=Integer.parseInt(header.group(1));
            Matcher app=ACTIVITY.matcher(sections[i]);
            if(app.find()) {
                foreground.put(id,new Foreground(Integer.parseInt(app.group(1)),app.group(2)));
                continue;
            }
            Matcher focus=CURRENT_FOCUS.matcher(sections[i]);
            if(!focus.find()) continue;
            int user=Integer.parseInt(focus.group(2));
            String component=focus.group(3), pkg=component.split("/",2)[0];
            for(Window w:windows) {
                if(w.display==id && w.token.equals(focus.group(1)) && w.uid/100000==user
                        && w.pkg.equals(pkg) && normalize(w.activity).equals(normalize(component))) {
                    foreground.put(id,new Foreground(user,component));
                    break;
                }
            }
        }
        return foreground;
    }

    static String field(String line,String key) {
        String prefix=key+"=";
        int start=line.startsWith(prefix)?0:line.indexOf(", "+prefix);
        if(start<0) return "";
        start+=prefix.length()+(start==0&&line.startsWith(prefix)?0:2);
        int end=line.indexOf(", ",start);
        return end<0?line.substring(start):line.substring(start,end);
    }

    static final class CommandRejected extends IOException {
        private static final long serialVersionUID=1L;
        CommandRejected(String executable) { super("设备命令失败："+executable); }
    }

    static String command(String... args) throws Exception {
        Process p=new ProcessBuilder(args).redirectErrorStream(true).start();
        try(BufferedReader in=new BufferedReader(new InputStreamReader(p.getInputStream(),StandardCharsets.UTF_8))) {
            StringBuilder out=new StringBuilder();
            String line;
            while((line=in.readLine())!=null) {
                if(out.length()+line.length()>4_000_000) throw new IOException("设备输出超限");
                out.append(line).append('\n');
            }
            String output=out.toString();
            if(p.waitFor()!=0) throw new CommandRejected(args[0]);
            if(output.contains("DUMP TIMEOUT")) throw new IOException("设备命令超时："+args[0]);
            return output;
        } finally { p.destroy(); }
    }

    interface GestureInput {
        long now() throws Exception;
        void verifyGeometry(Screen screen) throws Exception;
        boolean emit(Screen screen,int uid,long down,int action,int[][] points,boolean downPressure) throws Exception;
    }

    static final class TargetMismatch extends Exception {
        TargetMismatch(Throwable cause) { super(cause.getMessage(),cause); }
    }

    static boolean injectTarget(Method inject,Object input,Object event,int uid) throws Exception {
        try {
            return (boolean)inject.invoke(input,event,2,uid);
        } catch(InvocationTargetException wrapped) {
            Throwable cause=wrapped.getCause();
            if(cause instanceof IllegalArgumentException) {
                String message=cause.getMessage();
                if(message!=null && message.startsWith("Targeted input event injection from pid ")
                    && message.endsWith(" was not directed at a window owned by uid "+uid+"."))
                    throw new TargetMismatch(cause);
            }
            throw wrapped;
        }
    }

    static final class Android implements GestureInput {
        final Object displays, input;
        final Method ids, info, inject, uptime, obtain, setDisplay, recycle;
        final Constructor<?> properties, coords;
        final Field pointerId, toolType, x, y, pressure, size;
        final Class<?> propertiesType, coordsType;
        Android() throws Exception {
            Class<?> dmg=Class.forName("android.hardware.display.DisplayManagerGlobal");
            displays=dmg.getMethod("getInstance").invoke(null);
            Method list;
            try { list=dmg.getMethod("getDisplayIds"); }
            catch(NoSuchMethodException missing) { list=dmg.getMethod("getDisplayIds", boolean.class); }
            ids=list;
            info=dmg.getMethod("getDisplayInfo",int.class);
            Class<?> event=Class.forName("android.view.MotionEvent");
            Class<?> inputEvent=Class.forName("android.view.InputEvent");
            Class<?> binder=Class.forName("android.os.IBinder");
            Class<?> inputInterface=Class.forName("android.hardware.input.IInputManager");
            Object service=Class.forName("android.os.ServiceManager").getMethod("getService",String.class).invoke(null,"input");
            input=Class.forName("android.hardware.input.IInputManager$Stub").getMethod("asInterface",binder).invoke(null,service);
            Method targeted;
            try { targeted=inputInterface.getMethod("injectInputEventToTarget",inputEvent,int.class,int.class); }
            catch(NoSuchMethodException missing) { targeted=null; }
            inject=targeted;
            uptime=Class.forName("android.os.SystemClock").getMethod("uptimeMillis");
            propertiesType=Class.forName("android.view.MotionEvent$PointerProperties");
            coordsType=Class.forName("android.view.MotionEvent$PointerCoords");
            properties=propertiesType.getConstructor(); coords=coordsType.getConstructor();
            pointerId=propertiesType.getField("id"); toolType=propertiesType.getField("toolType");
            x=coordsType.getField("x"); y=coordsType.getField("y"); pressure=coordsType.getField("pressure"); size=coordsType.getField("size");
            obtain=event.getMethod("obtain",long.class,long.class,int.class,int.class,
                Array.newInstance(propertiesType,0).getClass(),Array.newInstance(coordsType,0).getClass(),
                int.class,int.class,float.class,float.class,int.class,int.class,int.class,int.class);
            setDisplay=event.getMethod("setDisplayId",int.class);
            recycle=event.getMethod("recycle");
        }
        boolean supported() throws Exception {
            return inject!=null && command("dumpsys","-t","2","package","com.android.shell")
                .contains("android.permission.INJECT_EVENTS: granted=true");
        }
        Map<Integer,Screen> screens(String wm,List<Window> windows) throws Exception {
            Map<Integer,Foreground> foreground=foregroundActivities(wm,windows);
            Map<Integer,Screen> values=new TreeMap<>();
            int[] ids=(int[])(this.ids.getParameterCount()==0 ? this.ids.invoke(displays) : this.ids.invoke(displays,false));
            for(int id:ids) {
                Object d=info.invoke(displays,id);
                if(d==null) throw new IllegalStateException("显示器信息已变化");
                Class<?> cls=d.getClass();
                Foreground app=foreground.get(id);
                values.put(id,new Screen(id,app==null?-1:app.user,cls.getField("logicalWidth").getInt(d),
                    cls.getField("logicalHeight").getInt(d),cls.getField("rotation").getInt(d),
                    cls.getField("state").getInt(d),String.valueOf(cls.getField("uniqueId").get(d)),app==null?"":app.activity));
            }
            return values;
        }
        Snapshot take() throws Exception {
            String wm=command("dumpsys","-t","2","window");
            List<Window> windows=parseWindows(command("dumpsys","-t","2","input"),wm);
            return new Snapshot(screens(wm,windows),windows);
        }
        @Override
        public long now() throws Exception { return (long)uptime.invoke(null); }
        @Override
        public void verifyGeometry(Screen screen) throws Exception {
            Object d=info.invoke(displays,screen.id);
            if(d==null||screen.rotation!=d.getClass().getField("rotation").getInt(d)
                ||screen.width!=d.getClass().getField("logicalWidth").getInt(d)
                ||screen.height!=d.getClass().getField("logicalHeight").getInt(d)
                ||screen.state!=d.getClass().getField("state").getInt(d)
                ||!screen.identity.equals(String.valueOf(d.getClass().getField("uniqueId").get(d))))
                throw new IllegalStateException("手势期间屏幕发生变化");
        }
        @Override
        public boolean emit(Screen screen,int uid,long down,int action,int[][] points,boolean downPressure) throws Exception {
            Object p=Array.newInstance(propertiesType,points.length), c=Array.newInstance(coordsType,points.length);
            for(int i=0;i<points.length;i++) {
                Object prop=properties.newInstance(), pos=coords.newInstance();
                pointerId.setInt(prop,i); toolType.setInt(prop,1);
                x.setFloat(pos,points[i][0]); y.setFloat(pos,points[i][1]);
                pressure.setFloat(pos,downPressure?1:0); size.setFloat(pos,1);
                Array.set(p,i,prop); Array.set(c,i,pos);
            }
            Object event=obtain.invoke(null,down,now(),action,points.length,p,c,0,0,1f,1f,0,0,0x1002,0);
            try {
                setDisplay.invoke(event,screen.id);
                return injectTarget(inject,input,event,uid);
            } finally { recycle.invoke(event); }
        }
    }

    static void send(String kind,Object... fields) {
        StringBuilder line=new StringBuilder("@ACPM\t").append(kind);
        for(Object v:fields) line.append('\t').append(String.valueOf(v).replaceAll("[\\r\\n\\t]"," "));
        System.out.println(line);
    }

    static void inspect(Android android) throws Exception {
        Snapshot snap=android.take();
        boolean supported=android.supported();
        for(Screen s:snap.screens.values()) {
            boolean touch=snap.windows.stream().anyMatch(w->w.display==s.id && !w.pkg.isEmpty());
            send("DISPLAY",s.id,s.user,s.width,s.height,s.rotation,s.state,s.identity,
                supported && touch && s.user>=0 && s.state==2,
                !supported?"系统未提供定向输入权限或接口":!touch?"无可识别的可触摸窗口":
                s.user<0?"无法确认屏幕所属用户":s.state!=2?"屏幕未打开":"",s.activity);
        }
    }

    static Map<Integer,Screen> checkTopology(Map<Integer,Screen> baseline,Snapshot current) {
        if(!baseline.keySet().equals(current.screens.keySet())) throw new IllegalStateException("屏幕拓扑已变化");
        for(Screen screen:baseline.values()) {
            Screen observed=current.screens.get(screen.id);
            // Activity transitions may temporarily hide the user. Input on that screen is skipped below.
            if(!screen.sameDisplay(observed) || (observed.user>=0 && observed.user!=screen.user))
                throw new IllegalStateException("屏幕属性或用户已变化："+screen.id);
        }
        return current.screens;
    }

    static int choose(int[] ratios,Random random) {
        int n=random.nextInt(100);
        for(int i=0;i<ratios.length;i++) { n-=ratios[i]; if(n<0) return i; }
        throw new IllegalArgumentException("事件占比无效");
    }

    static List<Integer> round(Set<Integer> ids,Random random) {
        List<Integer> next=new ArrayList<>(ids);
        Collections.sort(next);
        Collections.shuffle(next,random);
        return next;
    }

    static int[][][] points(Box box,Random random,int action) {
        int columns=box.r-box.l, rows=box.b-box.t;
        int x=box.l+random.nextInt(columns),y=box.t+random.nextInt(rows);
        if(action<2) return new int[][][]{{{x,y}}};
        if(action==2) {
            int endX=box.l+random.nextInt(columns),endY=box.t+random.nextInt(rows);
            if(Math.abs(endX-x)+Math.abs(endY-y)<8) return null;
            int[][][] p=new int[9][1][2];
            for(int i=0;i<9;i++) { p[i][0][0]=x+(endX-x)*i/8; p[i][0][1]=y+(endY-y)*i/8; }
            return p;
        }
        if(columns<32 || rows<16) return null;
        int center=box.l+columns/2, cy=box.t+rows/2;
        int extent=Math.min(60,columns/2-2);
        boolean expand=random.nextBoolean();
        int[][][] p=new int[9][2][2];
        for(int i=0;i<9;i++) {
            int distance=8+(extent-8)*(expand?i:8-i)/8;
            p[i][0]=new int[]{center-distance,cy}; p[i][1]=new int[]{center+distance,cy};
        }
        return p;
    }

    static void performGesture(GestureInput input,Screen screen,Window window,long sequence,
                               int action,int[][][] points,Path halt) throws Exception {
        try {
            boolean accepted=injectGesture(input,screen,window,action,points,halt);
            send("RESULT",sequence,screen.id,ACTIONS[action],accepted?"success":"skipped",window.pkg,
                accepted?Arrays.deepToString(points):"定向输入被系统拒绝");
        } catch(TargetMismatch mismatch) {
            send("RESULT",sequence,screen.id,ACTIONS[action],"skipped",window.pkg,
                "触摸窗口归属不匹配（目标 UID "+window.uid+"）");
        }
    }

    static boolean injectGesture(GestureInput input,Screen screen,Window window,
                                 int action,int[][][] points,Path halt) throws Exception {
        long down=input.now();
        int[][] active={points[0][0]};
        boolean pressed=false;
        try {
            if(Files.exists(halt)) throw new InterruptedException("收到停止请求");
            input.verifyGeometry(screen);
            if(!input.emit(screen,window.uid,down,0,active,true)) return false;
            pressed=true;
            if(action==1) Thread.sleep(650);
            if(action==3) {
                if(!input.emit(screen,window.uid,down,5|(1<<8),points[0],true)) return false;
                active=points[0];
            }
            for(int i=1;i<points.length;i++) {
                Thread.sleep(25);
                if(Files.exists(halt)) throw new InterruptedException("收到停止请求");
                input.verifyGeometry(screen);
                if(!input.emit(screen,window.uid,down,2,points[i],true)) return false;
                active=points[i];
            }
            if(Files.exists(halt)) throw new InterruptedException("收到停止请求");
            input.verifyGeometry(screen);
            if(action==3) {
                if(!input.emit(screen,window.uid,down,6|(1<<8),active,true)) return false;
                active=new int[][]{active[0]};
            }
            if(!input.emit(screen,window.uid,down,1,active,false)) return false;
            pressed=false;
            return true;
        } finally {
            if(pressed) {
                try { input.emit(screen,window.uid,down,3,active,false); }
                catch(TargetMismatch noLongerTargeted) { }
            }
        }
    }

    static void resolveApps(Target target, Screen screen) throws Exception {
        String installed=command("cmd","package","list","packages","-U","--user",Integer.toString(screen.user));
        for(String pkg:target.blacklist?target.launches:target.packages) {
            if(!PACKAGE.matcher(pkg).matches() || !installed.matches("(?s).*?\\bpackage:"+Pattern.quote(pkg)+"\\s+uid:\\d+.*"))
                throw new IllegalArgumentException("所属用户未安装指定应用："+pkg);
            String resolved=command("cmd","package","resolve-activity","--brief","--user",Integer.toString(screen.user),
                "-a","android.intent.action.MAIN","-c","android.intent.category.LAUNCHER",pkg);
            for(String line:resolved.split("\\R"))
                if(COMPONENT.matcher(line.trim()).matches() && line.trim().startsWith(pkg+"/"))
                    target.activities.put(pkg,line.trim());
        }
    }

    static String normalize(String component) {
        int slash=component.indexOf('/');
        if(slash<0) return component;
        String activity=component.substring(slash+1);
        return component.substring(0,slash+1)+(activity.startsWith(".")?component.substring(0,slash):"")+activity;
    }

    @FunctionalInterface
    interface DeviceCommand {
        String run(String... args) throws Exception;
    }

    static void switchApp(DeviceCommand execute,Screen screen,Target t,Random random,Map<Integer,Screen> before,
                          long sequence,Path halt) throws Exception {
        List<String> choices=new ArrayList<>(t.activities.values());
        if(screen.activity.contains("/")) choices.removeIf(c->c.startsWith(screen.activity.split("/",2)[0]+"/"));
        for(Screen other:before.values()) {
            if(other.id!=screen.id && other.activity.contains("/")) {
                String activePackage=other.activity.split("/",2)[0];
                choices.removeIf(c->c.startsWith(activePackage+"/"));
            }
        }
        if(choices.isEmpty()) {
            send("RESULT",sequence,screen.id,"app_switch","skipped","","没有其他可启动的指定应用");
            return;
        }
        String c=choices.get(random.nextInt(choices.size()));
        if(Files.exists(halt)) throw new InterruptedException("收到停止请求");
        boolean accepted;
        try {
            String output=execute.run("am","start","--display",""+screen.id,"--user",""+screen.user,"-n",c);
            accepted=!(output.contains("Error:") || output.contains("Error type ") || output.contains("Permission Denial"));
        } catch(CommandRejected refused) {
            accepted=false;
        }
        send("RESULT",sequence,screen.id,"app_switch",accepted?"success":"skipped",c,
            accepted?"已发出应用启动指令":"应用启动指令被设备拒绝");
    }

    static void run(Android a,Config c,Path gate,Path halt) throws Exception {
        if(!a.supported()) throw new IllegalStateException("缺少定向输入权限或接口");
        Snapshot first=a.take();
        Map<Integer,Screen> baseline=first.screens;
        for(Target t:c.targets.values()) {
            Screen s=baseline.get(t.id);
            if(s==null || s.user<0 || s.state!=2 || s.width<=0 || s.height<=0 || s.identity.equals("null")
                || s.user!=Integer.parseInt(t.expected[2]) || s.width!=Integer.parseInt(t.expected[3])
                || s.height!=Integer.parseInt(t.expected[4]) || s.rotation!=Integer.parseInt(t.expected[5])
                || !s.identity.equals(t.expected[6]))
                throw new IllegalStateException("屏幕配置已失效："+t.id);
            resolveApps(t,s);
            if(!t.blacklist && first.windows.stream().noneMatch(w->t.eligible(w,s)) && t.activities.isEmpty())
                throw new IllegalStateException("找不到允许的可触摸窗口或可启动应用："+t.id);
            for(List<Box> boxes:t.areas.values()) for(Box box:boxes)
                if(!box.valid() || box.l<0 || box.t<0 || box.r>s.width || box.b>s.height)
                    throw new IllegalArgumentException("系统区超出目标屏幕");
        }
        send("READY");
        if(gate==null) return;
        while(!Files.exists(gate) && !Files.exists(halt)) Thread.sleep(100);
        Random schedule=new Random(c.seed);
        Random geometry=new Random(c.seed ^ 0x5DEECE66DL);
        long sequence=0;
        while(!Files.exists(halt)) for(int id:round(c.targets.keySet(),schedule)) {
            if(Files.exists(halt)) return;
            long tick=a.now(), seq=++sequence;
            Target t=c.targets.get(id);
            int action=choose(c.ratios,schedule);
            send("PLAN",seq,id,ACTIONS[action]);
            try {
                Snapshot current=a.take();
                Screen s=checkTopology(baseline,current).get(id);
                if(Files.exists(halt)) throw new InterruptedException("收到停止请求");
                if(s.user<0) {
                    send("RESULT",seq,id,ACTIONS[action],"skipped","","当前屏幕用户暂不可确认");
                } else if(action==4) {
                    switchApp(Runner::command,s,t,geometry,current.screens,seq,halt);
                } else {
                    boolean attempted=false;
                    List<Window> candidates=new ArrayList<>();
                    for(Window w:current.windows) if(t.eligible(w,s)) candidates.add(w);
                    for(int attempt=0;attempt<32&&!candidates.isEmpty();attempt++) {
                        Window w=candidates.get(geometry.nextInt(candidates.size()));
                        List<Box> allowed=t.limits(w,s), boxes=w.boxes;
                        Box crop=allowed.get(geometry.nextInt(allowed.size())).intersect(boxes.get(geometry.nextInt(boxes.size())));
                        if(!crop.valid()) continue;
                        int[][][] points=points(crop,geometry,action);
                        if(points==null) continue;
                        boolean correct=true;
                        for(int[][] frame:points) for(int[] p:frame)
                            if(current.hit(id,p[0],p[1])!=w) correct=false;
                        if(!correct) continue;
                        performGesture(a,s,w,seq,action,points,halt);
                        attempted=true;
                        break;
                    }
                    if(!attempted) {
                        boolean blocked=t.blacklist && t.packages.contains(s.activity.split("/",2)[0]);
                        String detail=candidates.isEmpty()?"当前没有符合范围的触摸窗口":"可操作区域不足或被其他窗口覆盖";
                        send("RESULT",seq,id,ACTIONS[action],"skipped","",blocked?"当前应用在黑名单中":detail);
                    }
                }
            } catch(InterruptedException stopped) {
                send("RESULT",seq,id,ACTIONS[action],"skipped","",stopped.getMessage());
                return;
            } catch(Exception ex) {
                send("RESULT",seq,id,ACTIONS[action],"failed","",reason(ex));
                throw ex;
            }
            Thread.sleep(Math.max(0,c.throttle-(a.now()-tick)));
        }
    }

    static String reason(Throwable error) {
        while(error instanceof InvocationTargetException && error.getCause()!=null) error=error.getCause();
        return error.getMessage()==null?error.getClass().getSimpleName():error.getMessage();
    }

    public static void main(String[] args) {
        try {
            Android android=new Android();
            if(args[0].equals("inspect")) inspect(android);
            else if(args[0].equals("run") || args[0].equals("preflight"))
                run(android,Config.read(args[1]),args[0].equals("preflight")?null:Paths.get(args[2]),
                    args[0].equals("preflight")?null:Paths.get(args[3]));
            else throw new IllegalArgumentException("未知执行模式");
        } catch(Exception error) {
            send("ERROR",reason(error));
            System.exit(1);
        }
    }
}
